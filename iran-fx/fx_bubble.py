#!/usr/bin/env python3
"""
حباب دلار در بازار ایران، با مقایسهٔ سه کانال: دلار نقدی، درهم، تتر

ایده:
    درهم امارات به دلار پِگ است: 1 USD = 3.6725 AED (ثابت از ۱۹۹۷).
    پس «دلارِ ضمنیِ درهم» = قیمت درهم به تومان × 3.6725
    حباب دلار نقدی نسبت به کانال درهم = دلار بازار ÷ دلار ضمنی درهم − 1
    حباب تتر نسبت به کانال درهم       = تتر ÷ دلار ضمنی درهم − 1

نکته: این «حباب» نسبت به ارزش ذاتی نیست؛ اختلاف بین کانال‌های ارزی داخلی است.
مثبت بزرگ یعنی دلار نسبت به درهم گران خریده می‌شود (معمولاً هیجان/تقاضای خبری)،
منفی یعنی دلار نسبت به درهم عقب مانده.

حالت‌ها:
    live     گرفتن قیمت لحظه‌ای از منابع (باید از اینترنت ایران اجرا شود)
    manual   وارد کردن دستی سه قیمت
    history  تحلیل فایل CSV روزانه با ستون‌های date,usd,aed,usdt (تومان)

نمونه:
    python fx_bubble.py live --log data/fx_log.csv
    python fx_bubble.py manual --usd 104500 --aed 28150 --usdt 105200
    python fx_bubble.py history data/fx_daily.csv --window 20
"""
from __future__ import annotations

import argparse
import csv
import datetime as dt
import os
import sys

AED_PEG = 3.6725  # درهم در هر دلار

# آستانه‌های پیش‌فرض سیگنال (درصد). با بک‌تست روی دادهٔ خودت تنظیمشان کن.
HIGH_BUBBLE = 2.0
LOW_BUBBLE = -1.0


# ---------------------------------------------------------------- محاسبات
def implied_usd_from_aed(aed_toman: float) -> float:
    return aed_toman * AED_PEG


def bubble_pct(price: float, reference: float) -> float:
    return (price / reference - 1.0) * 100.0


def analyze(usd: float | None, aed: float, usdt: float | None) -> dict:
    ref = implied_usd_from_aed(aed)
    out = {"aed": aed, "usd_implied": ref, "usd": usd, "usdt": usdt}
    out["usd_bubble_pct"] = bubble_pct(usd, ref) if usd else None
    out["usdt_bubble_pct"] = bubble_pct(usdt, ref) if usdt else None
    out["usdt_vs_usd_pct"] = bubble_pct(usdt, usd) if (usd and usdt) else None
    return out


def verdict(b: float | None) -> str:
    if b is None:
        return "—"
    if b >= HIGH_BUBBLE:
        return "حباب مثبت (دلار نسبت به درهم گران)"
    if b <= LOW_BUBBLE:
        return "حباب منفی (دلار نسبت به درهم ارزان)"
    return "نزدیک تعادل"


def fmt(x: float | None, pct: bool = False) -> str:
    if x is None:
        return "—"
    return f"{x:+.2f}%" if pct else f"{x:,.0f}"


def print_report(r: dict, sources: dict | None = None) -> None:
    print(f"زمان: {dt.datetime.now():%Y-%m-%d %H:%M}")
    print(f"درهم:                 {fmt(r['aed'])} تومان")
    print(f"دلار ضمنی (×{AED_PEG}): {fmt(r['usd_implied'])} تومان")
    print(f"دلار بازار:           {fmt(r['usd'])} تومان")
    print(f"تتر:                  {fmt(r['usdt'])} تومان")
    print("-" * 44)
    print(f"حباب دلار نسبت به درهم: {fmt(r['usd_bubble_pct'], True)}  → {verdict(r['usd_bubble_pct'])}")
    print(f"حباب تتر نسبت به درهم:  {fmt(r['usdt_bubble_pct'], True)}  → {verdict(r['usdt_bubble_pct'])}")
    print(f"تتر نسبت به دلار نقدی:  {fmt(r['usdt_vs_usd_pct'], True)}")
    if sources:
        print("-" * 44)
        for k, v in sources.items():
            print(f"منبع {k}: {v}")


# ---------------------------------------------------------------- منابع لحظه‌ای
# همه قیمت‌ها به تومان برگردانده می‌شوند. ساختار پاسخ این APIها از خارج ایران
# قابل تست نبود؛ اگر منبعی خطا داد، پیام خطا را ببین و parser را اصلاح کن
# یا از حالت manual استفاده کن.
def _get_json(url: str) -> dict:
    import requests

    r = requests.get(url, timeout=15, headers={"User-Agent": "fx-bubble/1.0"})
    r.raise_for_status()
    return r.json()


def usdt_nobitex() -> float:
    j = _get_json("https://api.nobitex.ir/market/stats?srcCurrency=usdt&dstCurrency=rls")
    return float(j["stats"]["usdt-rls"]["latest"]) / 10  # ریال → تومان


def usdt_wallex() -> float:
    j = _get_json("https://api.wallex.ir/v1/markets")
    return float(j["result"]["symbols"]["USDTTMN"]["stats"]["lastPrice"])


def tgju_prices() -> dict:
    """دلار و درهم بازار آزاد از tgju (ریال → تومان)."""
    j = _get_json("https://call1.tgju.org/ajax.json")
    cur = j["current"]

    def p(key: str) -> float:
        return float(str(cur[key]["p"]).replace(",", "")) / 10

    return {"usd": p("price_dollar_rl"), "aed": p("price_aed")}


def fetch_live() -> tuple[dict, dict]:
    prices: dict = {}
    sources: dict = {}
    try:
        prices.update(tgju_prices())
        sources["دلار/درهم"] = "tgju"
    except Exception as e:  # noqa: BLE001
        sources["دلار/درهم"] = f"خطا: {e}"
    for name, fn in (("nobitex", usdt_nobitex), ("wallex", usdt_wallex)):
        try:
            prices["usdt"] = fn()
            sources["تتر"] = name
            break
        except Exception as e:  # noqa: BLE001
            sources[f"تتر ({name})"] = f"خطا: {e}"
    return prices, sources


def append_log(path: str, r: dict) -> None:
    new = not os.path.exists(path)
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["timestamp", "usd", "aed", "usdt", "usd_implied",
                        "usd_bubble_pct", "usdt_bubble_pct"])
        w.writerow([dt.datetime.now().isoformat(timespec="seconds"), r["usd"], r["aed"],
                    r["usdt"], round(r["usd_implied"]),
                    None if r["usd_bubble_pct"] is None else round(r["usd_bubble_pct"], 3),
                    None if r["usdt_bubble_pct"] is None else round(r["usdt_bubble_pct"], 3)])


# ---------------------------------------------------------------- تاریخچه
def analyze_history(path: str, window: int) -> "object":
    import pandas as pd

    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    if "aed" not in df.columns:
        sys.exit("فایل باید ستون aed داشته باشد (به‌علاوهٔ usd و/یا usdt).")
    df["usd_implied"] = df["aed"] * AED_PEG
    for col in ("usd", "usdt"):
        if col in df.columns:
            b = (df[col] / df["usd_implied"] - 1) * 100
            df[f"{col}_bubble_pct"] = b.round(3)
            mean = b.rolling(window).mean()
            std = b.rolling(window).std()
            df[f"{col}_bubble_z"] = ((b - mean) / std).round(2)
    return df


def main() -> None:
    ap = argparse.ArgumentParser(description="حباب دلار نسبت به درهم و تتر")
    sub = ap.add_subparsers(dest="mode", required=True)

    lv = sub.add_parser("live")
    lv.add_argument("--log", help="مسیر CSV برای ذخیرهٔ هر اجرا")

    mn = sub.add_parser("manual")
    mn.add_argument("--aed", type=float, required=True)
    mn.add_argument("--usd", type=float)
    mn.add_argument("--usdt", type=float)
    mn.add_argument("--log")

    hs = sub.add_parser("history")
    hs.add_argument("csv")
    hs.add_argument("--window", type=int, default=20)
    hs.add_argument("--out")

    a = ap.parse_args()

    if a.mode == "history":
        df = analyze_history(a.csv, a.window)
        print(df.tail(15).to_string(index=False))
        if "usd_bubble_pct" in df:
            s = df["usd_bubble_pct"].dropna()
            print(f"\nحباب دلار: میانگین {s.mean():+.2f}%  کمینه {s.min():+.2f}%  بیشینه {s.max():+.2f}%")
        if a.out:
            df.to_csv(a.out, index=False)
            print(f"ذخیره شد: {a.out}")
        return

    if a.mode == "live":
        prices, sources = fetch_live()
        if "aed" not in prices:
            print_report_err = "قیمت درهم دریافت نشد؛ از حالت manual استفاده کن."
            for k, v in sources.items():
                print(f"{k}: {v}")
            sys.exit(print_report_err)
        r = analyze(prices.get("usd"), prices["aed"], prices.get("usdt"))
        print_report(r, sources)
    else:
        r = analyze(a.usd, a.aed, a.usdt)
        print_report(r)

    if a.log:
        append_log(a.log, r)
        print(f"\nثبت شد در {a.log}")


if __name__ == "__main__":
    main()
