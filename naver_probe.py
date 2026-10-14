"""네이버 실시간·일봉 응답 구조 확인(2026-10-14) — 머니파이 15:30 이후 '오늘 봉' 가격 출처 정하기용"""
import json, requests, datetime as dt
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
kst = dt.datetime.utcnow() + dt.timedelta(hours=9)
print("KST", kst.strftime("%Y-%m-%d %H:%M:%S"))
today = kst.strftime("%Y%m%d")
for code in ("373220", "005930"):
    print("\n=====", code)
    for name, url in (("polling realtime", f"https://polling.finance.naver.com/api/realtime/domestic/stock/{code}"),
                      ("m.stock basic", f"https://m.stock.naver.com/api/stock/{code}/basic"),
                      ("m.stock integration", f"https://m.stock.naver.com/api/stock/{code}/integration")):
        try:
            r = requests.get(url, headers=H, timeout=20)
            print(f"--- {name} {r.status_code}")
            try:
                j = r.json()
                print(json.dumps(j, ensure_ascii=False, indent=1)[:6000])
            except Exception:
                print(r.text[:1500])
        except Exception as e:
            print(name, "실패", e)
    try:
        u = f"https://api.finance.naver.com/siseJson.naver?symbol={code}&requestType=1&startTime=20261001&endTime={today}&timeframe=day"
        t = requests.get(u, headers=H, timeout=20).text
        print("--- siseJson 마지막 4줄\n", "\n".join(l for l in t.strip().splitlines() if l.strip())[-400:])
    except Exception as e:
        print("siseJson 실패", e)
    for ys in (code + ".KS",):
        try:
            j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{ys}?range=5d&interval=1d", headers=H, timeout=20).json()
            res = j["chart"]["result"][0]; m = res["meta"]
            print("--- yahoo", ys, "regularMarketPrice", m.get("regularMarketPrice"), "time", m.get("regularMarketTime"),
                  "closes", res["indicators"]["quote"][0]["close"], "ts", res["timestamp"])
        except Exception as e:
            print("yahoo 실패", e)
    try:
        import FinanceDataReader as fdr
        print("--- FDR\n", fdr.DataReader(code, "2026-10-08").tail(4))
    except Exception as e:
        print("FDR 실패", e)
