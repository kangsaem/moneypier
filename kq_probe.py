"""상단 코스닥 카드 1일 그래프 기준선 확인(2026-10-21): 야후 ^KQ11·^KS11 1일 메타 vs 네이버 실시간"""
import json, urllib.request, urllib.parse
from datetime import datetime, timezone, timedelta
H = {"User-Agent": "Mozilla/5.0"}
def get(u):
    return json.loads(urllib.request.urlopen(urllib.request.Request(u, headers=H), timeout=20).read())
K = timezone(timedelta(hours=9))
for sym, nv in (("^KQ11", "KOSDAQ"), ("^KS11", "KOSPI")):
    try:
        j = get(f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(sym)}?range=1d&interval=2m")
        r = j["chart"]["result"][0]; m = r["meta"]
        ts, cl = r.get("timestamp") or [], r["indicators"]["quote"][0].get("close") or []
        pts = [(datetime.fromtimestamp(t, K).strftime("%m-%d %H:%M"), c) for t, c in zip(ts, cl) if c is not None]
        print(sym, "price", m.get("regularMarketPrice"), "previousClose", m.get("previousClose"), "chartPreviousClose", m.get("chartPreviousClose"),
              "regularMarketTime", datetime.fromtimestamp(m.get("regularMarketTime", 0), K))
        print("   points", len(pts), pts[:3], "...", pts[-3:])
    except Exception as e:
        print(sym, "야후 실패", e)
    try:
        n = get(f"https://polling.finance.naver.com/api/realtime/domestic/index/{nv}")["datas"][0]
        print("   naver", n.get("closePrice"), n.get("compareToPreviousClosePrice"), n.get("fluctuationsRatio"), n.get("localTradedAt"))
    except Exception as e:
        print("   naver 실패", e)
