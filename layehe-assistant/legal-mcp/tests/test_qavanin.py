from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from legal_mcp.browser import FetchError
from legal_mcp.qavanin import parse_amendments, parse_events, parse_info, parse_law, parse_related, parse_search, select_article

F = Path(__file__).parent / "fixtures" / "qavanin"


def read(name: str) -> str:
    return (F / name).read_text(encoding="utf-8")


def test_search_rows():
    r = parse_search(read("search.html"))
    assert r["total"] == 3 and r["pages"] == 1 and "آیین دادرسی" in r["description"]
    first, second = r["items"][0], r["items"][1]
    assert first == {
        "id": "1111111111111111111",
        "title": "قانون آیین دادرسی دادگاههای عمومی و انقلاب (در امور مدنی)",
        "approved": "1379/01/21",
        "approver": "مجلس شورای اسلامی",
    }
    assert second["repealed"] is True and second["approved"] == "1318/06/25"


def test_search_unrecognised_page():
    assert parse_search("<html><body>خطا</body></html>")["total"] is None


def test_law_sections():
    law = parse_law(read("law.html"))
    assert law["approved"] == "1379/01/21" and law["approver"] == "مجلس شورای اسلامی"
    labels = [s["label"] for s in law["sections"]]
    assert labels == ["فصل سوم", "ماده 84", "تبصره", "ماده 85", "ماده 85 مکرر", "ماده 86"]
    a84 = law["sections"][1]
    assert a84["status"] == "amended" and a84["history"] is True
    assert a84["text"].startswith("در موارد زیر") and "\n10- خواهان" in a84["text"]
    assert "نظریه مشورتی" not in a84["text"]
    assert a84["opinions"] == [{"id": "9999999999999999999", "title": "نظریه مشورتی شماره 7/1400/123"}]
    assert law["sections"][4]["status"] == "repealed"


def test_law_without_tree_raises():
    with pytest.raises(ValueError):
        parse_law("<html><body>no</body></html>")


def test_select_article():
    secs = parse_law(read("law.html"))["sections"]
    assert [s["label"] for s in select_article(secs, "۸۴")] == ["ماده 84", "تبصره"]
    assert [s["label"] for s in select_article(secs, "ماده 85")] == ["ماده 85"]
    assert [s["label"] for s in select_article(secs, "85 مکرر")] == ["ماده 85 مکرر"]
    with pytest.raises(FetchError):
        select_article(secs, "8")
    with pytest.raises(FetchError):
        select_article(secs, "بدون شماره")


def test_info():
    info = parse_info(read("attribute.html"))
    assert info["type"] == "عادی" and info["approved"] == "1355/04/16" and info["gazette_no"] == "9205"
    assert info["status"] == "معتبر" and "effective" not in info
    assert info["executors"] == ["قوه قضاییه", "بانک مرکزی"]


def test_amendments_and_events():
    r = parse_amendments(read("status.html"), 30)
    assert r["amended_by"] == [{"id": "5555", "title": "قانون اصلاح قانون صدور چک", "date": "1397/08/13"}]
    assert r["amends"] == [] and r["amends_total"] == 0
    art = r["articles"][0]
    assert art["section_id"] == "7001" and art["label"] == "ماده 3" and art["changes"] == 1
    assert art["latest"]["type"] == "اصلاحی" and art["latest"]["date"] == "1397/08/13"
    assert r["articles"][1]["changes"] == 0
    ev = parse_events(BeautifulSoup(read("status.html"), "html.parser"))[0]
    assert ev["previous"] == "متن قدیم ماده سه" and ev["new_text"] == "متن جدید ماده سه"
    assert ev["by"]["id"] == "5555"


def test_related():
    r = parse_related(read("related.html"), 40)
    assert [x["id"] for x in r["laws"]] == ["6001"]
    assert r["others_total"] == 2 and r["others"][0]["date"] == "1400/05/12"
