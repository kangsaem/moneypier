"""후보 종목 시세 확인(2026-10-12, lab-bt 랜덤 후보용): ticker_check_list.json 의 미장 티커(야후)·국장 코드(네이버 siseJson) 중
5년 전(2021-01)부터 시세가 있는지 → results/ticker_check.json"""
import json
import time

import requests
import yfinance as yf

L = json.load(open("ticker_check_list.json"))
out = {"us": {}, "kr": {}}
for t in L["us"]:
    sym = t.replace(".", "-")
    try:
        h = yf.Ticker(sym).history(start="2020-12-01", end="2021-03-01", auto_adjust=False)
        r = yf.Ticker(sym).history(period="5d", auto_adjust=False)
        out["us"][t] = {"old": len(h), "now": len(r)}
    except Exception as e:
        out["us"][t] = {"err": str(e)[:80]}
    time.sleep(0.2)
H = {"User-Agent": "Mozilla/5.0"}
for c in L["kr"]:
    try:
        u = f"https://api.finance.naver.com/siseJson.naver?symbol={c}&requestType=1&startTime=20201201&endTime=20210301&timeframe=day"
        a = requests.get(u, headers=H, timeout=20).text.count("[") - 1
        u2 = f"https://api.finance.naver.com/siseJson.naver?symbol={c}&requestType=1&startTime=20260901&endTime=20261012&timeframe=day"
        b = requests.get(u2, headers=H, timeout=20).text.count("[") - 1
        out["kr"][c] = {"old": a, "now": b}
    except Exception as e:
        out["kr"][c] = {"err": str(e)[:80]}
    time.sleep(0.1)
with open("results/ticker_check.json", "w") as f:
    json.dump(out, f, indent=0)
bad_us = [t for t, v in out["us"].items() if not v.get("old") or not v.get("now")]
bad_kr = [c for c, v in out["kr"].items() if v.get("old", 0) <= 0 or v.get("now", 0) <= 0]
print("미장 문제", len(bad_us), bad_us)
print("국장 문제", len(bad_kr), bad_kr)
