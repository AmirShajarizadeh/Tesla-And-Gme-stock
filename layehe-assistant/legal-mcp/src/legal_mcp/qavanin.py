"""Laws: search and read qavanin.ir (سامانه ملی قوانین و مقررات جمهوری اسلامی ایران).

Pages used, all server-rendered HTML:
  /                         search (query string parameters below)
  /Law/TreeText/?IDS=       full text as sections; TextDate= for an earlier version, WithOpinion=true adds opinions
  /Law/Attribute/?IDS=      metadata
  /Law/StatusIndex/?IDS=    amending and amended laws, amended articles
  /Law/SectionStatus/<id>   history of one section
  /Law/RelatedIndex/?IDS=   related laws, and regulations/votes/opinions citing the law
Parsing is by BeautifulSoup with loose matching; when a page does not have the expected shape the tools return the
page as plain text (`page_text`) instead of failing, so a markup change degrades rather than breaks them.
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import Annotated, Any, Literal
from urllib.parse import urlencode

from bs4 import BeautifulSoup, Tag
from pydantic import Field

from .app import READ_ONLY, mcp, respond
from .browser import FetchError, fetch
from .extract import ascii_digits, fa, main_text, page_slice

BASE = "https://qavanin.ir"
IDS = Annotated[str, Field(pattern=r"^\d{1,25}$", description="Document id from law_search (digits, as a string).")]
JALALI = re.compile(r"1[2-4]\d\d/\d\d/\d\d")

KIND_PARAM = {"law": "_isLaw", "regulation": "_isRegulation", "vote": "_IsVote", "opinion": "_isOpenion"}
MATCH_MODE = {"all_words": 1, "phrase": 2, "part_of_word": 3, "any_word": 4}
STATUS_CODE = {"valid": 1, "trial": 3, "temporary": 4, "repealed": 7, "moot": 9, "enforced": 14, "extended": 15}
SORTS = {"newest": ("APPROVEDATE", "True"), "oldest": ("APPROVEDATE", "False"), "title": ("CAPTION", "False")}
PAGE_SIZES = (25, 50, 100)
# Curated lists on the site (Report_ID); the useful ones for court work.
LISTS = {
    1: "قوانین کاربردی",
    115: "آرای دیوان عالی کشور",
    116: "آرای دیوان عدالت اداری",
    126: "نظرات مشورتی اداره کل حقوقی قوه قضائیه",
    120: "بخشنامه‌های قوه قضائیه",
    113: "آخرین قوانین",
    114: "آخرین مقررات",
    121: "قوانین منسوخ",
}
SECTION_STATUS = {"green": "amended", "red": "repealed", "brown": "opinion"}
HEADING = re.compile(r"^(ماده|فصل|مبحث|باب|کتاب|قسمت|بخش|مقدمه)")
META_LABELS = {
    "نوع قانون": "type",
    "مرجع تصویب": "approver",
    "تاریخ تصویب": "approved",
    "شماره ابلاغ": "notification_no",
    "تاریخ ابلاغ": "notified",
    "مرجع ابلاغ": "notified_by",
    "شماره روزنامه رسمی": "gazette_no",
    "تاریخ روزنامه رسمی": "gazette_date",
    "آخرین وضعیت": "status",
    "تاریخ اجرا": "effective",
    "طبقه بندی": "classification",
}
LAW_CACHE_TTL = 600

_cache: dict[tuple, tuple[float, Any]] = {}


# ---------------------------------------------------------------- helpers


def _txt(el: Tag | None) -> str:
    if el is None:
        return ""
    return fa(re.sub(r"\s+", " ", el.get_text(" ", strip=True))).strip()


def _url(path: str, params: dict[str, Any] | None = None) -> str:
    return BASE + path + ("?" + urlencode(params, doseq=True) if params else "")


def _law_url(ids: str) -> str:
    return _url("/Law/TreeText/", {"IDS": ids})


def _ids_of(href: str) -> str | None:
    m = re.search(r"IDS=(\d+)", href or "")
    return m[1] if m else None


def _jalali(value: str | None, name: str) -> str | None:
    if not value:
        return None
    v = ascii_digits(value).strip().replace("-", "/")
    if not re.fullmatch(r"\d{4}/\d{1,2}/\d{1,2}", v):
        raise FetchError(f"{name} must be a Jalali date like 1402/07/15, got {value!r}.")
    y, m, d = v.split("/")
    return f"{y}/{int(m):02d}/{int(d):02d}"


async def _cached(key: tuple, make: Any) -> Any:
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < LAW_CACHE_TTL:
        return hit[1]
    value = await make()
    while len(_cache) >= 24:
        _cache.pop(next(iter(_cache)))
    _cache[key] = (time.monotonic(), value)
    return value


def _fallback(html: str, limit: int = 6000) -> str:
    return main_text(html)["text"][:limit]


# ---------------------------------------------------------------- parsers (pure, tested offline)


def parse_search(html: str) -> dict[str, Any]:
    """Total, page count, the site's own description of the filters, and result rows."""
    soup = BeautifulSoup(html, "html.parser")
    text = ascii_digits(fa(soup.get_text(" ", strip=True)))
    total = re.search(r"تعداد یافته ها\s*:\s*(\d+)", text)
    desc = re.search(r"توصیف جستجو\s*:\s*(.*?)(?:تعداد یافته ها|$)", text)
    pager = soup.find("select", id="PageNumber")
    rows = []
    for tr in soup.find_all("tr"):
        link = tr.find("a", href=re.compile(r"IDS=\d+"))
        cells = tr.find_all("td")
        if not link or len(cells) < 3:
            continue
        values = [_txt(c) for c in cells]
        title = _txt(link) or max(values, key=len)
        approved = next((ascii_digits(v) for v in values if JALALI.fullmatch(ascii_digits(v))), None)
        rest = [v for v in values if v and v != title and ascii_digits(v) != approved and not v.isdigit()]
        row: dict[str, Any] = {"id": _ids_of(link["href"]), "title": title, "approved": approved}
        if rest:
            row["approver"] = rest[-1]
        if "red" in (tr.get("class") or []):
            row["repealed"] = True
        rows.append(row)
    return {
        "total": int(total[1]) if total else None,
        "pages": len(pager.find_all("option")) if pager else None,
        "description": desc[1].strip() if desc else "",
        "items": rows,
    }


def _section_label(p: Tag) -> tuple[str, Tag | None]:
    for span in p.find_all("span"):
        classes = span.get("class") or []
        if "bold" in classes and "blue" in classes:
            return _txt(span), span
    return "", None


def parse_law(html: str) -> dict[str, Any]:
    """Title, approval line and the flat list of sections (articles, notes, headings) of a TreeText page."""
    soup = BeautifulSoup(html, "html.parser")
    root = soup.find(id="treeText")
    if root is None:
        raise ValueError("no treeText")
    head = soup.find("h1", class_="mytitle") or soup.find("h1")
    sub = soup.find("h2", class_="Titr")
    out: dict[str, Any] = {"title": _txt(head)}
    if sub and (m := re.search(r"مصوب\s*(1[2-4]\d\d/\d\d/\d\d)\s*(.*)", ascii_digits(_txt(sub)))):
        out["approved"] = m[1]
        if m[2].strip():
            out["approver"] = m[2].strip()
    sections = []
    for p in root.find_all("p", class_=lambda c: bool(c) and "SecTex" in c):
        if not p.get("id") or p.find(class_="rounddiv"):
            continue  # the title block
        node: dict[str, Any] = {"id": p["id"]}
        status = next((SECTION_STATUS[c] for c in p.get("class", []) if c in SECTION_STATUS), None)
        if status:
            node["status"] = status
        if p.find("img", src=re.compile(r"arm\.gif")):
            node["history"] = True
        opinions = []
        for extra in p.find_all(lambda t: t.name == "fieldset" or (t.name == "div" and (
                str(t.get("id", "")).startswith("share") or "w100" in (t.get("class") or [])))):  # fmt: skip
            for a in extra.find_all("a", href=re.compile(r"TreeText/\?IDS=\d+")):
                opinions.append({"id": _ids_of(a["href"]), "title": _txt(a)})
            extra.decompose()
        label, span = _section_label(p)
        if span is not None:
            span.decompose()
        for br in p.find_all("br"):
            br.replace_with("\n")
        body = fa(p.get_text(""))
        body = "\n".join(re.sub(r"[ \t\xa0]+", " ", line).strip() for line in body.split("\n"))
        node["label"] = label
        node["text"] = re.sub(r"^\s*-\s*", "", re.sub(r"\n{3,}", "\n\n", body).strip())
        if opinions:
            node["opinions"] = opinions[:5]
        sections.append(node)
    out["sections"] = sections
    return out


def select_article(sections: list[dict[str, Any]], article: str) -> list[dict[str, Any]]:
    """One article and everything under it (notes, paragraphs) up to the next article or heading."""
    a = ascii_digits(fa(article))
    num = re.search(r"\d+", a)
    if not num:
        raise FetchError(f"article must include a number, e.g. '84' or '10 مکرر', got {article!r}.")
    repeat = "مکرر" in a
    want = re.compile(rf"^ماده\s*{num[0]}(?!\d)")
    for i, s in enumerate(sections):
        label = ascii_digits(s.get("label", ""))
        if want.match(label) and (("مکرر" in label) == repeat):
            end = i + 1
            while end < len(sections) and not HEADING.match(sections[end].get("label", "")):
                end += 1
            return sections[i:end]
    raise FetchError(f"Article {num[0]} not found among {len(sections)} sections; read the law without `article`.")


def parse_info(html: str) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {"title": _txt(soup.find("h1", class_="mytitle") or soup.find("h1"))}
    for lab in soup.find_all(class_="text-primary"):
        key = META_LABELS.get(fa(_txt(lab)).rstrip(":").strip())
        if not key or key in out:
            continue
        holder = lab.find_parent("div")
        value_el = holder.find_next_sibling() if holder else None
        value = re.sub(r"\s*-\s*$", "", _txt(value_el))
        if value and value not in ("-", "ندارد", "0000/00/00"):
            out[key] = ascii_digits(value) if key in ("approved", "notified", "gazette_date", "effective") else value
    for h in soup.find_all("h4"):
        heading = _txt(h)
        key = "executors" if "مجری" in heading else "copy_to" if "رونوشت" in heading else None
        ul = h.find_next("ul")
        if key and ul:
            out[key] = [_txt(li) for li in ul.find_all("li")][:30]
    return out


def _link_rows(container: Tag) -> list[dict[str, Any]]:
    rows = []
    for tr in container.find_all("tr"):
        a = tr.find("a", href=re.compile(r"TreeText/\?IDS=\d+"))
        if not a:
            continue
        date = JALALI.search(ascii_digits(_txt(tr)))
        rows.append({"id": _ids_of(a["href"]), "title": _txt(a), "date": date[0] if date else None})
    return rows


def parse_events(fragment: Tag | BeautifulSoup) -> list[dict[str, Any]]:
    """Changes of one section (oldest first) from the Box1 blocks of a SectionStatus page."""
    events = []
    for box in fragment.find_all("div", class_="Box1"):
        parts = [box.get_text(" ", strip=True)]
        sib = box.find_next_sibling()
        while sib is not None and not (sib.name == "div" and "Box1" in (sib.get("class") or [])):
            parts.append(sib.get_text(" ", strip=True) if isinstance(sib, Tag) else str(sib))
            sib = sib.find_next_sibling()
        text = ascii_digits(fa(re.sub(r"\s+", " ", " ".join(parts))))
        ev: dict[str, Any] = {}
        if m := re.search(r"نوع وضعیت\s*:\s*(.*?)\s*(?:توسط|تاریخ\s*:|متن قبلی|$)", text):
            ev["type"] = m[1]
        if m := re.search(r"تاریخ\s*:\s*(1[2-4]\d\d/\d\d/\d\d)", text):
            ev["date"] = m[1]
        if a := box.find("a", href=re.compile(r"IDS=\d+")):
            ev["by"] = {"id": _ids_of(a["href"]), "title": _txt(a)}
        if m := re.search(r"متن قبلی\s*:\s*(.*?)(?=متن اثرگذار|$)", text):
            ev["previous"] = m[1].strip()[:1500]
        if m := re.search(r"متن اثرگذار/?\s*تغییر یافته\s*:\s*(.*)", text):
            ev["new_text"] = m[1].strip()[:1500]
        events.append(ev)
    return events


def parse_amendments(html: str, limit: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}
    tables = soup.find_all("table", class_="slwTable")
    for key, table in zip(("amended_by", "amends"), tables[:2]):
        rows = _link_rows(table)
        out[key], out[f"{key}_total"] = rows[:limit], len(rows)
    articles = []
    for panel in soup.find_all("div", class_="panel"):
        if not str(panel.get("id", "")).isdigit():
            continue
        btn = panel.find("a", attrs={"role": "button"})
        events = parse_events(panel)
        art: dict[str, Any] = {"section_id": panel["id"], "label": _txt(btn), "changes": len(events)}
        if events:
            art["latest"] = {k: events[-1][k] for k in ("type", "date", "by") if k in events[-1]}
        articles.append(art)
    out["articles"] = articles
    return out


def parse_related(html: str, limit: int) -> dict[str, Any]:
    soup = BeautifulSoup(html, "html.parser")
    out: dict[str, Any] = {}
    for h in soup.find_all("h2", class_="Titr"):
        heading = _txt(h)
        key = "others" if "مقررات" in heading else "laws" if "قوانین" in heading else None
        if not key or key in out:
            continue
        table = h.find_next("table")
        rows = _link_rows(table) if table else []
        out[key], out[f"{key}_total"] = rows[:limit], len(rows)
    return out


# ---------------------------------------------------------------- tools


@mcp.tool(title="Search laws, votes and opinions", annotations=READ_ONLY, structured_output=False)
async def law_search(
    query: Annotated[str | None, Field(max_length=200, description="Persian words, e.g. 'آیین دادرسی مدنی'.")] = None,
    in_text: Annotated[bool, Field(description="Search the full text, not only titles (no snippets).")] = False,
    match: Annotated[
        Literal["all_words", "phrase", "part_of_word", "any_word"], Field(description="How the words must match.")
    ] = "all_words",
    kind: Annotated[
        Literal["law", "regulation", "vote", "opinion"] | None,
        Field(description="law: statutes; regulation: آیین‌نامه/مصوبه; vote: court votes (وحدت رویه...); opinion: نظر مشورتی."),
    ] = None,
    status: Annotated[Literal["valid", "repealed", "temporary", "trial"] | None, Field(description="Legal status.")] = None,
    list_id: Annotated[
        int | None,
        Field(description="Curated list: 115 Supreme Court votes, 116 Administrative Court votes, 126 advisory opinions, 120 judiciary circulars, 1 key laws."),
    ] = None,
    approved_from: Annotated[str | None, Field(description="Jalali YYYY/MM/DD.")] = None,
    approved_to: Annotated[str | None, Field(description="Jalali YYYY/MM/DD.")] = None,
    sort: Annotated[Literal["newest", "oldest", "title"], Field(description="Order of results.")] = "newest",
    page: Annotated[int, Field(ge=1, le=1000)] = 1,
    limit: Annotated[int, Field(ge=1, le=100)] = 20,
) -> str:
    """Search Iranian laws, regulations, court votes and advisory opinions on qavanin.ir.

    Returns total and items (id, title, approved, approver, repealed). Open one with law_read(ids=id).
    For a well-known law, search its exact title with kind='law' (e.g. 'قانون مدنی').
    """

    async def run() -> dict[str, Any]:
        col, desc = SORTS[sort]
        params: dict[str, Any] = {
            "size": next(s for s in PAGE_SIZES if s >= limit),
            "PageNumber": page,
            "SortColumn": col,
            "SortDesc": desc,
        }
        q = fa(query or "").strip()
        if q:
            params |= {"CAPTION": q, ("IsTextSearch" if in_text else "IsTitleSearch"): "true"}
            params["SeachTextType"] = MATCH_MODE[match]  # the site's own spelling
        if kind:
            params[KIND_PARAM[kind]] = "true"
        if status:
            params["LawStatus"] = STATUS_CODE[status]
        if list_id:
            params["Report_ID"] = list_id
        if v := _jalali(approved_from, "approved_from"):
            params["fromApproveDate"] = v
        if v := _jalali(approved_to, "approved_to"):
            params["APPROVEDATE"] = v
        filtered = len(params) > 4
        url = _url("/", params)
        # The site now and then answers a filtered search with the unfiltered list and no filter description.
        for attempt in range(3):
            html, _ = await fetch(url)
            res = parse_search(html)
            if not filtered or res["description"] or res["total"] == 0:
                break
            await asyncio.sleep(1 + attempt)
        else:
            res["warning"] = "The site may have ignored the filters; check that the titles match the query."
        if res["total"] is None:
            return {"url": url, "items": [], "page_text": _fallback(html), "note": "Result page not recognised."}
        items = res["items"][:limit]
        for it in items:
            it["url"] = _law_url(it["id"])
        out = {"total": res["total"], "page": page, "pages": res["pages"], "items": items}
        if res["description"]:
            out["filters"] = res["description"]
        if "warning" in res:
            out["warning"] = res["warning"]
        return out

    return await respond(run())


@mcp.tool(title="Read a law, vote or opinion", annotations=READ_ONLY, structured_output=False)
async def law_read(
    ids: IDS,
    article: Annotated[
        str | None, Field(max_length=40, description="Only this article with its notes, e.g. '84' or '10 مکرر'.")
    ] = None,
    date: Annotated[
        str | None, Field(description="Jalali YYYY/MM/DD: the text in force on that date (e.g. the date of the event).")
    ] = None,
    with_opinions: Annotated[bool, Field(description="Add interpretive opinions attached to sections.")] = False,
    offset: Annotated[int, Field(ge=0, description="Sections to skip (next_offset of the previous call).")] = 0,
    max_chars: Annotated[int, Field(ge=1000, le=100000)] = 15000,
) -> str:
    """Read the text of a law, regulation, court vote or opinion from qavanin.ir as sections.

    Each section: id, label (ماده ۱۰، تبصره ۱ ...), text, status (amended / repealed / opinion), history (true when
    article_history has changes). Large laws page by offset/next_offset; prefer `article` for one article.
    """

    async def run() -> dict[str, Any]:
        d = _jalali(date, "date")
        params: dict[str, Any] = {"IDS": ids}
        if d:
            params["TextDate"] = d
        if with_opinions:
            params["WithOpinion"] = "true"
        url = _url("/Law/TreeText/", params)

        async def load() -> dict[str, Any]:
            html, _ = await fetch(url)
            try:
                return parse_law(html)
            except ValueError:
                return {"title": main_text(html)["title"], "sections": [], "page_text": main_text(html)["text"]}

        law = await _cached(("law", ids, d, with_opinions), load)
        out: dict[str, Any] = {"id": ids, "title": law.get("title"), "url": _law_url(ids)}
        for k in ("approved", "approver"):
            if law.get(k):
                out[k] = law[k]
        if d:
            out["text_as_of"] = d
        if not law["sections"]:
            return out | {"note": "Sections not recognised; plain text follows."} | page_slice(
                law.get("page_text", ""), 0, max_chars
            )
        sections = select_article(law["sections"], article) if article else law["sections"]
        out["sections_total"] = len(sections)
        picked, used = [], 0
        for i in range(offset, len(sections)):
            s = dict(sections[i])
            room = max_chars - used
            if picked and len(s["text"]) > room:
                out["next_offset"] = i
                break
            if len(s["text"]) > room:
                s["text"] = s["text"][:room] + "…"
                out["cut"] = "One section is longer than max_chars; raise max_chars to read all of it."
            used += len(s["text"])
            picked.append(s)
        out["sections"] = picked
        return out

    return await respond(run())


@mcp.tool(title="Law metadata", annotations=READ_ONLY, structured_output=False)
async def law_info(ids: IDS) -> str:
    """Type, approving body, approval date, notification and official gazette numbers and dates, last status,
    executing bodies of a law. Use it to cite a law exactly and to check it is still in force."""

    async def run() -> dict[str, Any]:
        html, _ = await fetch(_url("/Law/Attribute/", {"IDS": ids}))
        info = parse_info(html)
        out = {"id": ids} | info | {"url": _law_url(ids)}
        if len(info) <= 1:
            out["page_text"] = _fallback(html, 3000)
        return out

    return await respond(run())


@mcp.tool(title="Amendments of a law", annotations=READ_ONLY, structured_output=False)
async def law_amendments(ids: IDS, limit: Annotated[int, Field(ge=1, le=100)] = 30) -> str:
    """Laws that amended this law, laws this law amends, and which of its articles changed (with section ids for
    article_history)."""

    async def run() -> dict[str, Any]:
        html, _ = await fetch(_url("/Law/StatusIndex/", {"IDS": ids}))
        res = parse_amendments(html, limit)
        out = {"id": ids} | res
        if not res.get("amended_by") and not res.get("amends") and not res.get("articles"):
            out["page_text"] = _fallback(html, 4000)
        return out

    return await respond(run())


@mcp.tool(title="History of one article", annotations=READ_ONLY, structured_output=False)
async def article_history(
    section_id: Annotated[str, Field(pattern=r"^\d{1,15}$", description="Section id from law_read or law_amendments.")],
) -> str:
    """Every change of one article, oldest first: type (اصلاحی، الحاقی، نسخ ...), date, amending law, previous and
    new text."""

    async def run() -> dict[str, Any]:
        html, _ = await fetch(_url(f"/Law/SectionStatus/{section_id}"))
        events = parse_events(BeautifulSoup(html, "html.parser"))
        return {"section_id": section_id, "events": events}

    return await respond(run())


@mcp.tool(title="Votes, opinions and regulations citing a law", annotations=READ_ONLY, structured_output=False)
async def law_related(ids: IDS, limit: Annotated[int, Field(ge=1, le=200)] = 40) -> str:
    """Related laws, and regulations, court votes (آرای وحدت رویه، دیوان عدالت اداری) and advisory opinions that cite
    this law. Read any of them with law_read."""

    async def run() -> dict[str, Any]:
        html, _ = await fetch(_url("/Law/RelatedIndex/", {"IDS": ids}))
        res = parse_related(html, limit)
        out = {"id": ids} | res
        if not res:
            out["page_text"] = _fallback(html, 4000)
        return out

    return await respond(run())
