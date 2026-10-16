"""휴장일 확인 로직 점검(2026-10-16): 장이 안 열린 날(주말·휴장일)에 네이버 코스피 실시간의 localTradedAt이
직전 거래일 날짜로 나오는지. build_page._kr_traded_today()가 이 값의 날짜 == 오늘이면 개장일로 봄.
주말에 실행해 휴장일과 같은 상황을 확인한다. 결과: results/holiday_probe.txt"""
from datetime import datetime, timedelta, timezone
import json
import re
import requests

H = {"User-Agent": "Mozilla/5.0", "Referer": "https://finance.naver.com/"}
kst = datetime.now(timezone.utc) + timedelta(hours=9)
today = kst.strftime("%Y-%m-%d")
print("실행 시각(KST):", kst.strftime("%Y-%m-%d %H:%M"), ["월", "화", "수", "목", "금", "토", "일"][kst.weekday()])

try:
    j = requests.get("https://polling.finance.naver.com/api/realtime/domestic/index/KOSPI", headers=H, timeout=20).json()
    d = (j.get("datas") or [{}])[0]
    at = d.get("localTradedAt", "")
    print("localTradedAt:", at, "| marketStatus:", d.get("marketStatus"), "| closePrice:", d.get("closePrice"))
    print("판정(_kr_traded_today):", None if not at else at[:10] == today, "(오늘 장이 안 열렸으면 False여야 정상)")
except Exception as e:
    print("실시간 조회 실패:", e)

try:
    st = (kst - timedelta(days=10)).strftime("%Y%m%d")
    t = requests.get(f"https://api.finance.naver.com/siseJson.naver?symbol=005930&requestType=1&startTime={st}"
                     f"&endTime={kst:%Y%m%d}&timeframe=day", headers=H, timeout=20).text
    ds = re.findall(r'\["(\d{8})"', t)
    print("삼성전자 일봉 마지막 날짜:", ds[-3:] if ds else "없음")
except Exception as e:
    print("일봉 조회 실패:", e)

try:
    j = requests.get("https://m.stock.naver.com/api/index/KOSPI/basic", headers=H, timeout=20).json()
    print("m.stock basic:", json.dumps({k: j.get(k) for k in ("localTradedAt", "marketStatus", "closePrice")}, ensure_ascii=False))
except Exception as e:
    print("m.stock basic 실패:", e)
