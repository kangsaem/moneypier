"""외국인 순매수(코스피) 과거 자료 경로 찾기(2026-10-15)"""
import requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36", "Referer": "https://m.stock.naver.com/"}
for u in ["https://m.stock.naver.com/api/index/KOSPI/trend?bizdate=20261013",
          "https://m.stock.naver.com/api/index/KOSPI/trend?bizdate=20200320",
          "https://m.stock.naver.com/api/index/KOSPI/trend?bizdate=20080102",
          "https://m.stock.naver.com/api/index/KOSPI/trend?bizdate=19990104",
          "https://m.stock.naver.com/api/index/KOSPI/trend?pageSize=60&page=2",
          "https://m.stock.naver.com/api/index/KOSPI/trends?pageSize=20&page=1",
          "https://m.stock.naver.com/api/index/KOSPI/investorTrends?pageSize=20&page=1",
          "https://m.stock.naver.com/api/index/KOSPI/investor?pageSize=20&page=1",
          "https://m.stock.naver.com/api/index/KOSPI/dealTrend?pageSize=20&page=1",
          "https://m.stock.naver.com/front-api/index/trend?indexCode=KOSPI&pageSize=20&page=1",
          "https://m.stock.naver.com/front-api/index/investorTrend?code=KOSPI&pageSize=20&page=1",
          "https://api.stock.naver.com/index/KOSPI/trend?pageSize=20&page=1",
          "https://api.stock.naver.com/index/KOSPI/investor/trend?pageSize=20&page=1"]:
    try:
        r = requests.get(u, headers=H, timeout=20)
        print("=====", u, r.status_code, r.headers.get("content-type", "")[:30], r.text[:400].replace("\n", " ") if "json" in r.headers.get("content-type", "") else len(r.text))
    except Exception as e:
        print(u, "실패", e)
