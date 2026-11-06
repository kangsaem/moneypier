"""지수 200일선 위치 확인(2026-11-06) — 머니파이 V1과 같은 야후 일봉(분할만 반영 종가)으로 최근 15거래일 종가·200일선·차이 → results/ix200_check.txt"""
import time, requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36"}
out = []
for sym in ("^IXIC", "^NDX", "^GSPC", "^KS11"):
    p2 = int(time.time()); p1 = p2 - 3 * 365 * 86400
    j = requests.get(f"https://query1.finance.yahoo.com/v8/finance/chart/{sym}?period1={p1}&period2={p2}&interval=1d", headers=H, timeout=30).json()
    r = j["chart"]["result"][0]; off = r["meta"].get("gmtoffset", 0)
    rows = [(time.strftime("%Y-%m-%d", time.gmtime(t + off)), c) for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]) if c is not None]
    cl = [c for _, c in rows]
    out.append(f"== {sym}  meta 현재가 {r['meta'].get('regularMarketPrice')}  봉 {len(cl)}개")
    for i in range(len(cl) - 15, len(cl)):
        ma = sum(cl[i - 199:i + 1]) / 200
        out.append(f"{rows[i][0]}  종가 {cl[i]:,.2f}  200일선 {ma:,.2f}  {(cl[i] / ma - 1) * 100:+.2f}%  {'아래' if cl[i] < ma else '위'}")
open("results/ix200_check.txt", "w").write("\n".join(out) + "\n")
print("\n".join(out))
