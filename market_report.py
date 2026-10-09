"""
장 마감 후 실행용 시장 리포트
설치: pip install finance-datareader pykrx yfinance pandas requests
사용: python market_report.py 005930
"""
import sys
from datetime import datetime, timedelta

import pandas as pd
import requests
import FinanceDataReader as fdr

TICKER = sys.argv[1] if len(sys.argv) > 1 else "005930"  # 기본: 삼성전자
NAME = ""  # 표시용 종목명 (build_page.py가 채워줌)
START = (datetime.today() - timedelta(days=365 * 12)).strftime("%Y-%m-%d")


def safe(label, fn):
    """항목별로 실패해도 나머지는 계속 출력"""
    try:
        fn()
    except Exception as e:
        print(f"  [{label}] 가져오기 실패: {type(e).__name__}: {e}")


# ---------- 공통 함수 ----------
def fmt_price(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}"


def load_prices(code):
    """국내 6자리 코드는 FinanceDataReader, 해외/실패 시 yfinance로 일봉 조회"""
    try:
        df = fdr.DataReader(code, START)
        if len(df):
            return df
    except Exception:
        pass
    import yfinance as yf
    df = yf.Ticker(code).history(start=START, auto_adjust=True)
    df.index = df.index.tz_localize(None)
    return df



def rsi(close, n=14):
    d = close.diff()
    g = d.clip(lower=0).ewm(alpha=1 / n, adjust=False).mean()
    l = (-d.clip(upper=0)).ewm(alpha=1 / n, adjust=False).mean()
    return 100 - 100 / (1 + g / l)


def cross_info(close, short, long, unit):
    """단기/장기 이평선의 이격(%)과 가장 최근 교차 정보"""
    s = close.rolling(short).mean()
    l = close.rolling(long).mean()
    gap = (s / l - 1) * 100
    sign = (s > l).astype(int)[l.notna()]
    changed = sign[sign.diff().fillna(0) != 0]
    print(f"  {short}{unit}선 vs {long}{unit}선: 이격 {gap.iloc[-1]:+.2f}% "
          f"({'단기선이 위(정배열)' if s.iloc[-1] > l.iloc[-1] else '단기선이 아래(역배열)'})")
    if len(changed):
        last_date = changed.index[-1]
        kind = "골든크로스" if changed.iloc[-1] == 1 else "데드크로스"
        bars_ago = len(sign.loc[last_date:]) - 1
        print(f"     최근 교차: {kind} / {last_date.date()} ({bars_ago}{unit} 전)")


# ---------- 1. 해당 종목 ----------
def stock_report():
    df = load_prices(TICKER)
    c = df["Close"]
    print(f"\n===== {NAME} {TICKER} (기준일 {df.index[-1].date()}, 종가 {fmt_price(c.iloc[-1])}) =====")

    print(f"RSI(14): {rsi(c).iloc[-1]:.1f}")

    print("\n[일봉] ")
    cross_info(c, 5, 10, "일")

    weekly = c.resample("W-FRI").last().dropna()
    print("[주봉]")
    cross_info(weekly, 5, 10, "주")

    monthly = c.resample("ME").last().dropna()
    print("[월봉]  (요청하신 '5주선·10월선'은 5월선·10월선으로 해석)")
    cross_info(monthly, 5, 10, "월")

    ma50 = c.rolling(50).mean()
    disp = (c / ma50 * 100).dropna()
    print(f"\n50일 이격도: 현재 {disp.iloc[-1]:.1f}")
    print(f"  조회 기간({disp.index[0].date()}~) 최대 {disp.max():.1f} ({disp.idxmax().date()}) / "
          f"최저 {disp.min():.1f} ({disp.idxmin().date()})")
    pct = (disp < disp.iloc[-1]).mean() * 100
    print(f"  현재 이격도는 과거 분포의 하위 {pct:.0f}% 위치")

    high52 = df["High"].rolling(250, min_periods=1).max().iloc[-1]
    print(f"\n52주 최고가(장중 고가 기준): {high52:,.0f} → 현재 {((c.iloc[-1] / high52) - 1) * 100:+.1f}%")

    r5 = (c.iloc[-1] / c.iloc[-6] - 1) * 100
    print(f"최근 5거래일 등락률: {r5:+.2f}%")
    print("  일별:", ", ".join(f"{x:+.1f}%" for x in c.pct_change().tail(5) * 100))


# ---------- 2. 코스피 ----------
def kospi_report():
    print("\n===== 코스피 =====")
    ks = fdr.DataReader("KS11", START)["Close"]
    d = (ks / ks.rolling(50).mean() * 100).dropna()
    print(f"코스피 {ks.iloc[-1]:,.2f} / 50일 이격도 {d.iloc[-1]:.1f} "
          f"(과거 최대 {d.max():.1f}, 최저 {d.min():.1f})")

    def pbr():
        from pykrx import stock
        end = datetime.today().strftime("%Y%m%d")
        begin = (datetime.today() - timedelta(days=10)).strftime("%Y%m%d")
        f = stock.get_index_fundamental(begin, end, "1001")  # 1001 = 코스피
        print(f"코스피 PBR {f['PBR'].iloc[-1]:.2f} / PER {f['PER'].iloc[-1]:.2f} ({f.index[-1].date()})")

    safe("코스피 PBR(pykrx)", pbr)

    def vkospi():
        v = fdr.DataReader("VKOSPI", START)["Close"]
        print(f"VKOSPI(코스피 변동성지수) {v.iloc[-1]:.2f} ({v.index[-1].date()})")

    safe("VKOSPI", vkospi)


# ---------- 3. 미국 ----------
def us_report():
    import yfinance as yf
    print("\n===== 미국 =====")

    def last(sym, name, unit=""):
        h = yf.Ticker(sym).history(period="10d")["Close"].dropna()
        print(f"{name}: {h.iloc[-1]:.2f}{unit} ({h.index[-1].date()}, 전일 대비 {h.iloc[-1] - h.iloc[-2]:+.2f})")

    safe("VIX", lambda: last("^VIX", "VIX"))
    safe("미국채 10년물", lambda: last("^TNX", "미국채 10년물 금리", "%"))
    safe("미국채 30년물", lambda: last("^TYX", "미국채 30년물 금리", "%"))

    def fng():
        url = "https://production.dataviz.cnn.io/index/fearandgreed/graphdata"
        j = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=10).json()["fear_and_greed"]
        print(f"공포탐욕지수(CNN): {j['score']:.0f} ({j['rating']})")

    safe("공포탐욕지수", fng)


if __name__ == "__main__":
    safe("종목", stock_report)
    safe("코스피", kospi_report)
    safe("미국", us_report)
