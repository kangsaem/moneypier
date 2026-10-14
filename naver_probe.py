"""네이버 코스피 지수 실시간 응답 확인(2026-10-14) — 리포트 개장일 확인(localTradedAt) 필드 점검"""
import json, requests, datetime as dt
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
print("KST", (dt.datetime.utcnow() + dt.timedelta(hours=9)).strftime("%Y-%m-%d %H:%M:%S"))
for code in ("KOSPI", "KOSDAQ"):
    r = requests.get(f"https://polling.finance.naver.com/api/realtime/domestic/index/{code}", headers=H, timeout=20)
    print("=====", code, r.status_code)
    print(json.dumps(r.json(), ensure_ascii=False, indent=1)[:4000])
