from pathlib import Path

from legal_mcp.extract import fa, is_challenge, main_text, page_slice, result_links, site_of

F = Path(__file__).parent / "fixtures"


def read(name: str) -> str:
    return (F / name).read_text(encoding="utf-8")


def test_result_links_keeps_only_ruling_rows():
    items = result_links(read("search.html"), "https://ara.jri.ac.ir/Judge/Index?Text=x")
    urls = [i["url"] for i in items]
    assert urls == [
        "https://ara.jri.ac.ir/Judge/Text/10234",
        "https://ara.jri.ac.ir/Judge/Text/10567",
        "https://ara.jri.ac.ir/Judge/Text/11001",
        "https://ara.jri.ac.ir/Judge/Text/11002?x=1",
    ]
    assert items[0]["title"] == "مطالبه وجه چک و خسارت تاخیر تادیه"  # Arabic letters normalised
    assert "تجديدنظر" in items[0]["snippet"] or "تجدیدنظر" in items[0]["snippet"]


def test_result_links_limit():
    assert len(result_links(read("search.html"), "https://ara.jri.ac.ir/", limit=2)) == 2


def test_main_text_picks_ruling_body():
    doc = main_text(read("ruling.html"))
    assert doc["title"] == "مطالبه وجه چک"
    assert "ماده ۳۵۸" in doc["text"]
    assert "این رای قطعی است" in doc["text"]
    assert "تماس با ما" not in doc["text"]
    assert "var x" not in doc["text"]


def test_challenge_detection():
    assert is_challenge(read("challenge.html"))
    assert not is_challenge(read("ruling.html"))


def test_page_slice_pages_through_text():
    text = ("جمله‌ای نمونه. " * 300).strip()
    first = page_slice(text, 0, 1000)
    assert first["next_offset"] <= 1000 and str(first["text"]).endswith(".")
    rest = page_slice(text, first["next_offset"], 100000)
    assert "next_offset" not in rest
    assert str(first["text"]) + str(rest["text"]) == text


def test_site_of_and_fa():
    assert site_of("ara.jri.ac.ir") == "jri.ac.ir"
    assert site_of("www.qavanin.ir") == "qavanin.ir"
    assert site_of("rc.majlis.ir") == "majlis.ir"
    assert fa("قانون مدني") == "قانون مدنی"
