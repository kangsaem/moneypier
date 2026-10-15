"""네이버 투자자별 매매동향(코스피) 확인(2026-10-15) — 외국인 일별 순매수 형식·과거 범위"""
import requests, re, datetime as dt
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36", "Referer": "https://finance.naver.com/"}
def get(u):
    r = requests.get(u, headers=H, timeout=20); r.encoding = "euc-kr"; return r
for u in ["https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=20261014&sosok=&page=1",
          "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=20261014&sosok=01&page=1",
          "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=20000104&sosok=01&page=1",
          "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=19980105&sosok=01&page=1",
          "https://finance.naver.com/sise/investorDealTrendDay.naver?bizdate=20261014&sosok=01&page=300",
          "https://m.stock.naver.com/api/index/KOSPI/trend?pageSize=20&page=1",
          "https://m.stock.naver.com/api/index/KOSPI/investorTrend?pageSize=20&page=1"]:
    try:
        r = get(u); t = r.text
        print("=====", u, r.status_code, len(t))
        txt = re.sub(r"<script.*?</script>|<style.*?</style>", " ", t, flags=re.S)
        txt = re.sub(r"<[^>]+>", " ", txt); txt = re.sub(r"[ \t\r\n]+", " ", txt)
        i = txt.find("날짜"); print(txt[max(0, i - 100): i + 1500] if i >= 0 else txt[:1500])
    except Exception as e:
        print(u, "실패", e)
