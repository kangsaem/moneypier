"""
장 마감 리포트: 1) 시장 지표  2) 종목 스크리닝  3) 종목별 세부내용
설치: pip install finance-datareader yfinance pandas requests
사용: python market_report.py 005930 000660 AAPL
"""
import contextlib
import io
import json
import os
import sys
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr

START = (datetime.today() - timedelta(days=365 * 12)).strftime("%Y-%m-%d")

# ===== 스크리닝 기준 (여기 숫자만 바꾸면 됩니다) =====
CROSS_DAYS = 5      # '최근 N거래일 이내' 골든/데드크로스
MOVE_PCT = 8.0      # 최근 5거래일 누적 등락 또는 하루 등락이 이 % 이상이면 포함
CROSS_NEAR = 0.7   # 5일선이 10일선 아래 몇 % 이내면 '골든크로스 임박'으로 볼지
FALL_PCT = -5.0    # 최근 5일 등락이 이 값 이하이거나 20일 신저가 갱신 중이면 '하락 진행 중'
NEAR_MAX = 0.9      # 50일 이격도가 (과거 최대 × 0.9) 이상이면 '높은 구간'(매도 조건). 고정값(110 등)은 쓰지 않음
NEAR_MIN = 1.1      # 50일 이격도가 (과거 최소 × 1.1) 이하이면 '낮은 구간'(매수 조건). 고정값(95 등)은 쓰지 않음
ADD_DISP_MAX = 90   # 불타기: 50일 이격도가 과거 최대 대비 이 % 미만이어야 "과열 아님" (매도 쪽 상단 기준과 동일)
RSI_HI = 70         # 매도 과열 기준 RSI (기본)
RSI_HI_STRONG = 80  # 일·주·월 모두 정배열(정·정·정, 강한 상승)일 때의 매도 과열 기준
RSI_LO = 35         # 매수 '싸다' 기준 RSI (기본)
RSI_LO_WEAK = 30    # 일·주·월 모두 역배열(역·역·역, 하락 추세)일 때의 매수 기준
CHART_DAYS = 132    # 차트에 보여줄 기간(거래일). 큰 차트·소형 차트·코스피 공통
BOTTOM_DROP = 25.0  # 바닥 단계: 1년 고점 대비 이 % 이상 빠진 저점이 있어야 '바닥'을 따짐
BOTTOM_WIN = 120    # 바닥 단계: 저점 뒤 이 거래일까지만 바닥 단계로 봄
BOTTOM_QUIET = 20   # 바닥 단계: 저점 뒤 이 거래일 동안 신저가가 없어야 '하락 멈춤' (10일도 시험했으나 LS에선 더 빠르지 않았음 — 백테스트로 확인 예정)
TOP_RISE = 50.0     # 꼭지 단계: 1년 저점 대비 이 % 이상 오른 고점이 있어야 '꼭지'를 따짐
TOP_WIN = 120       # 꼭지 단계: 고점 뒤 이 거래일까지만 꼭지 단계로 봄
TOP_QUIET = 10      # 꼭지 단계: 고점 뒤 이 거래일 동안 신고가가 없어야 '상승 멈춤'
TOP_HOLD = "low"    # 꼭지 단계: 고점을 넘기 전까지 유지
TOP_FAST = True     # 꼭지 1/3 빠른 판정: 고점 무렵(고점 포함 6거래일) RSI 과열(70 이상)이거나 50일 이격도가 과거 최대 근접(×NEAR_MAX)이면
                    #   기다리지 않고 10일선 아래 첫 종가에 1/3
BOTTOM_FAST = False # 바닥 1/3 빠른 판정(이격도 과거 최소 근접 시 기다리지 않음) — LS 시험에서 폭락 중 반등마다 켜져 꺼 둠(2026-10-10)
BOTTOM_HOLD = "low"  # 바닥 단계 유지 방식: "low" = 저점을 깨기 전까지 유지 / "ma20" = 종가가 20일선 아래로 가면 해제 / "" = 매일 새로 판정
ADD_TOUCH = 2.0     # 불타기: 최근 5일 안에 종가가 20일선 위 이 % 이내까지 내려왔으면 '눌림'


# ---------------------------------------------------------------- 데이터
def fmt_price(x):
    return f"{x:,.0f}" if x >= 1000 else f"{x:,.2f}"


def load_prices(code):
    """국내 코드는 FinanceDataReader, 해외/실패 시 yfinance로 일봉 조회"""
    base = code.split(".")[0] if code.upper().endswith((".KS", ".KQ")) else code
    try:
        with contextlib.redirect_stdout(io.StringIO()):  # fdr이 찍는 오류 문구는 숨김
            df = fdr.DataReader(base, START)
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


def make_bars(c, rule):
    """주봉/월봉 종가. 날짜는 그 기간의 실제 마지막 거래일로 표시"""
    tmp = pd.DataFrame({"close": c.values, "date": c.index}, index=c.index)
    try:
        g = tmp.groupby(pd.Grouper(freq=rule)).last().dropna()
    except ValueError:  # 구버전 pandas는 'ME' 대신 'M'
        g = tmp.groupby(pd.Grouper(freq="M" if rule == "ME" else rule)).last().dropna()
    return pd.Series(g["close"].values, index=pd.DatetimeIndex(g["date"]))


def cross_state(close, short=5, long=10):
    """단기/장기 이평 이격, 현재 정/역배열, 가장 최근 전환 정보"""
    s = close.rolling(short).mean()
    l = close.rolling(long).mean()
    gap = (s / l - 1) * 100
    if pd.isna(gap.iloc[-1]):
        return {"ok": False, "n": len(close)}
    above = (s > l)[l.notna()]
    sign = above.astype(int)
    changed = sign[sign.diff().fillna(0) != 0]
    out = {"ok": True, "gap": float(gap.iloc[-1]), "above": bool(above.iloc[-1]), "cross": None}
    if len(changed):
        d = changed.index[-1]
        out["cross"] = {"date": d, "golden": bool(changed.iloc[-1] == 1), "ago": len(sign.loc[d:]) - 1}
    return out


def bar_ma(c, n, rule):
    """일봉 위에 그리는 주봉/월봉 이동평균. 증권사 앱의 주봉·월봉 차트처럼 각 주(월)의 마지막 거래일에
    n봉 이평값을 찍고 그 사이를 직선으로 잇는다(계단 없음). 마지막 점은 진행 중인 봉 = 오늘 값이라
    cross_state의 주/월 배열 판정과 일치한다."""
    ma = make_bars(c, rule).rolling(n).mean().dropna()
    s = pd.Series(float("nan"), index=c.index)
    s.loc[ma.index] = ma.values
    return s.interpolate(limit_area="inside")


def future_days(c):
    """앞으로 보여줄 날짜: 내일부터 한 달 뒤까지의 평일(공휴일은 무시한 근사)"""
    last = c.index[-1]
    end = last + pd.DateOffset(months=1)
    return pd.bdate_range(last + pd.Timedelta(days=1), end)


def bar_ma_proj(c, n, rule, fut):
    """가격이 오늘 종가에 그대로 머문다고 가정한 주봉/월봉 이평의 앞으로의 경로.
    오늘 값에서 출발해, 이후 각 주(월) 마지막 날의 예상값을 찍고 직선으로 잇는다."""
    ext = pd.concat([c, pd.Series(float(c.iloc[-1]), index=fut)])
    ma = make_bars(ext, rule).rolling(n).mean()
    pts = ma[ma.index > c.index[-1]].dropna()
    s = pd.Series(float("nan"), index=pd.DatetimeIndex([c.index[-1]]).append(fut))
    s.iloc[0] = bar_ma(c, n, rule).iloc[-1]
    s.loc[pts.index] = pts.values
    return [None if pd.isna(v) else float(v) for v in s.interpolate(limit_area="inside")]


def chart_data(c, rs=None, n=None, with_proj=True):
    """차트용 최근 n거래일 데이터: 종가, 5/20일선, 5/10주선, 5/10월선(전체 기간으로 계산 후 자름), RSI.
    이동평균은 5·20일, 5·10주, 5·10월만 쓴다(30·150·300일선은 쓰지 않음)."""
    t = c.tail(n or CHART_DAYS)
    def lst(x):
        return [None if pd.isna(v) else float(v) for v in x.reindex(t.index)]
    fut = future_days(c) if with_proj else pd.DatetimeIndex([])   # 백테스트(가벼운 모드)는 점선 경로 생략
    proj = {}
    specs = (("w5", 5, "W-FRI"), ("w10", 10, "W-FRI"), ("m5", 5, "ME"), ("m10", 10, "ME"))
    for k, nn, rule in (specs if with_proj else ()):
        try:
            proj[k] = bar_ma_proj(c, nn, rule, fut)      # 길이 = 1(오늘) + len(fut)
        except Exception:
            proj[k] = None
    return {"dates": [d.strftime("%y.%m.%d") for d in t.index], "close": [float(v) for v in t],
            "fdates": [d.strftime("%y.%m.%d") for d in fut], "proj": proj,
            "w5": lst(bar_ma(c, 5, "W-FRI")), "w10": lst(bar_ma(c, 10, "W-FRI")),
            "m5": lst(bar_ma(c, 5, "ME")), "m10": lst(bar_ma(c, 10, "ME")),
            "ma5": lst(c.rolling(5).mean()), "ma20": lst(c.rolling(20).mean()),
            "rsi": lst(rs) if rs is not None else None}


def analyze(code, name):
    """종목 하나의 모든 지표를 계산해 dict로 반환"""
    return analyze_df(load_prices(code), code, name)


def analyze_df(df, code, name, light=False):
    """시세 DataFrame(Close, High)으로 지표·신호 계산. 백테스트는 과거 시점까지 자른 df를 넣는다.
    light=True면 화면용 점선 경로 계산을 생략(신호 결과는 같음)"""
    c = df["Close"].dropna()
    if len(c) < 30:
        raise ValueError("시세 데이터가 부족하거나 조회 실패")
    rs = rsi(c)
    r = {"code": code, "name": name, "last": c.index[-1], "close": float(c.iloc[-1]),
         "rsi": float(rs.iloc[-1]), "rsi_prev": float(rs.iloc[-2]),
         "ma10": float(c.rolling(10).mean().iloc[-1]), "rsi_min5": float(rs.tail(5).min()), "rsi_max5": float(rs.tail(5).max()),
         "day": cross_state(c), "week": cross_state(make_bars(c, "W-FRI")),
         "month": cross_state(make_bars(c, "ME"))}

    disp = (c / c.rolling(50).mean() * 100).dropna()
    r["disp"] = None
    if len(disp):
        cur, mx, mn = float(disp.iloc[-1]), float(disp.max()), float(disp.min())
        up, dn = cur / mx, cur / mn   # 현재값이 과거 최대/최소의 몇 배인지
        r["disp"] = {"cur": cur, "max": mx, "max_date": disp.idxmax(), "min": mn, "min_date": disp.idxmin(),
                     "up": up, "down": dn, "max_thr": mx * NEAR_MAX, "min_thr": mn * NEAR_MIN, "pct": float((disp < cur).mean() * 100),
                     "months": len(disp) / 21, "since": disp.index[0]}

    high = df["High"].dropna() if "High" in df else c
    h52 = float(high.rolling(250, min_periods=1).max().iloc[-1])
    r["high52"], r["from_high"] = h52, (r["close"] / h52 - 1) * 100

    rets = (c.pct_change().tail(5) * 100).tolist()
    r["rets"] = rets
    r["ret5"] = float((c.iloc[-1] / c.iloc[-6] - 1) * 100) if len(c) >= 6 else 0.0
    r["maxday"] = max(rets, key=abs) if rets else 0.0
    r["chart"] = chart_data(c, rs, with_proj=not light)
    newlow = c <= c.shift(1).rolling(20).min()          # 직전 20일 저가를 깬 날
    r["new_low3"] = bool(newlow.tail(3).any())          # 최근 3일 안에 신저가를 냈는가
    r["bottom"], r["bottom_info"] = bottom_stage(c, rs, r)
    r["top"], r["top_info"] = top_stage(c, rs, r)
    if r["bottom"] and r["top"]:                     # 둘 다 켜지면 더 최근 극점 쪽만 남김(급등 뒤 급락처럼 둘 다 조건을 채울 때)
        if r["bottom_info"]["ext_date"] > r["top_info"]["ext_date"]:
            r["top"], r["top_info"] = 0, {}
        else:
            r["bottom"], r["bottom_info"] = 0, {}
    r["sig"] = stock_signals(r)
    return r


# ---------------------------------------------------------------- 종목 목록 (리포트·백테스트 공용)
def load_tickers():
    raw = os.environ.get("TICKERS_JSON", "").strip()
    if raw:
        return json.loads(raw)
    with open("tickers.json", encoding="utf-8") as f:
        return json.load(f)


def resolve_codes(items):
    """코드 없이 이름만 있는 종목(금액만 입력한 경우)은 KRX 종목/ETF 목록에서 이름으로 찾는다"""
    if all(t.get("code") for t in items):
        return items
    names = {}
    try:
        import FinanceDataReader as fdr
        for market, col in (("KRX", "Code"), ("ETF/KR", "Symbol")):
            try:
                lst = fdr.StockListing(market)
                names.update(dict(zip(lst["Name"], lst[col])))
            except Exception as e:
                print(f"[{market} 목록] 실패: {e}")
    except Exception as e:
        print(f"[종목 목록] 실패: {e}")
    for t in items:
        if not t.get("code"):
            t["code"] = names.get(t["name"], "")
    return items


def _turn_stage(c, rs, top=False):
    """바닥(top=False)·꼭지(top=True) 단계 공용 계산. 꼭지는 바닥 규칙을 위아래로 뒤집은 것.
    바닥: 1년 고점 이후 최저 종가 = 극점, 그 고점 대비 BOTTOM_DROP% 이상 하락 / 꼭지: 1년 저점 이후 최고 종가 = 극점, 그 저점 대비 TOP_RISE% 이상 상승.
    1/3 = 극점 뒤 QUIET거래일 이상 새 극점 없음 + 종가가 10일선 위(바닥)/아래(꼭지)
          빠른 판정(BOTTOM_FAST/TOP_FAST): 극점 무렵 50일 이격도가 과거 최소(바닥)·최대(꼭지)에 근접했으면(꼭지는 RSI 과열도)
          기다리지 않고 10일선을 넘는(바닥)·깨는(꼭지) 첫 종가에 1/3
    2/3 = 1/3 + (쌍바닥·쌍봉: 극점에서 10% 이상 되돌린 뒤 그 폭의 60% 이상 다시 극점 쪽으로 와서 극점의 10% 안
            또는 RSI 다이버전스: 두 번째 극점이 극점의 5% 안인데 RSI는 5 이상 덜 극단) + 20일선 방향 전환
    3/3 = 1/3 조건 + 주봉 5/10 배열 전환(바닥 5주>10주, 꼭지 5주<10주) + 종가가 10주선 위/아래 + 10주선 방향 멈춤
    유지(HOLD="low"): 한 번 켜진 단계는 극점을 깨기 전까지 유지. 극점 다음 날부터 오늘까지 하루씩 훑어서 판정."""
    info = {}
    if len(c) < 260:
        return 0, info
    need, win, quiet, hold_mode = ((TOP_RISE, TOP_WIN, TOP_QUIET, TOP_HOLD) if top
                                   else (BOTTOM_DROP, BOTTOM_WIN, BOTTOM_QUIET, BOTTOM_HOLD))
    yr = c.iloc[-250:]
    if top:
        d0, A = yr.idxmin(), float(yr.min())         # 최근 1년 저점
        seg0 = c.loc[d0:]
        d1, E = seg0.idxmax(), float(seg0.max())     # 그 뒤 최고 종가 = 꼭지 후보
        move = (E / A - 1) * 100
        ok = move >= need
    else:
        d0, A = yr.idxmax(), float(yr.max())         # 최근 1년 고점
        seg0 = c.loc[d0:]
        d1, E = seg0.idxmin(), float(seg0.min())     # 그 뒤 최저 종가 = 바닥 후보
        move = (E / A - 1) * 100
        ok = move <= -need
    since = len(c.loc[d1:]) - 1
    if not ok or since > win:
        return 0, info
    n = len(c)
    i1 = n - 1 - since
    tail = c.iloc[-(since + 90):]                    # 극점 전 여유분(20일선·10주선 계산용)
    cv = c.values
    off = n - len(tail)
    m10 = tail.rolling(10).mean().values
    m20 = tail.rolling(20).mean().values
    per = tail.index.to_period("W-FRI")
    wk_close = tail.groupby(per).last()

    def asof(nb):                                    # 그날 기준 주봉 n개 평균 = (직전 완성 주봉 n-1개 + 그날 종가) / n
        prev = wk_close.rolling(nb - 1).sum().shift(1).reindex(per).values
        return (tail.values + prev) / nb
    w5s, w10s = asof(5), asof(10)
    rv = rs.values
    sg = -1 if top else 1                            # 꼭지는 부호를 뒤집어 같은 비교를 씀
    # 빠른 판정: 극점 무렵(극점 포함 6거래일)에 50일 이격도가 그때까지의 과거 최대(꼭지)·최소(바닥)에 근접했거나, 꼭지는 RSI 과열
    d50 = (c / c.rolling(50).mean() * 100).values
    lo_ = max(0, i1 - 5)
    near = False
    if top and TOP_FAST:
        hist = pd.Series(d50[: i1 + 1]).cummax().values
        near = any(d50[k] == d50[k] and d50[k] >= hist[k] * NEAR_MAX for k in range(lo_, i1 + 1)) or max(rv[lo_:i1 + 1]) >= RSI_HI
    elif not top and BOTTOM_FAST:
        hist = pd.Series(d50[: i1 + 1]).cummin().values
        near = any(d50[k] == d50[k] and d50[k] <= hist[k] * NEAR_MIN for k in range(lo_, i1 + 1))
    fast = bool(near)
    P, E2, d2 = E, None, None                        # P = 극점 뒤 되돌림의 끝(바닥: 반등 고점, 꼭지: 눌림 저점), E2 = 그 뒤 두 번째 극점
    lvl, flags = 0, {}
    for j in range(i1, n):
        x = cv[j]
        if sg * x > sg * P:
            P, E2, d2 = x, None, None
        elif j > i1 and (E2 is None or sg * x < sg * E2):
            E2, d2 = x, j
        k = j - off
        a10, a20 = m10[k], m20[k]
        raw1 = ((j - i1) >= quiet or fast) and sg * x >= sg * a10
        retest = div = False
        moved = (P >= E * 1.10) if not top else (P <= E * 0.90)                  # 극점에서 10% 이상 되돌림
        if moved and E2 is not None and sg * E2 <= sg * (P - (P - E) * 0.6):    # 그 폭의 60% 이상 다시 극점 쪽으로
            retest = (E2 <= E * 1.10) if not top else (E2 >= E * 0.90)
            div = ((E2 <= E * 1.05 and rv[d2] > rv[i1] + 5) if not top else (E2 >= E * 0.95 and rv[d2] < rv[i1] - 5))
        m20_turn = k >= 5 and sg * a20 > sg * m20[k - 5]
        w5, w10 = w5s[k], w10s[k]
        w10_flat = k >= 5 and w10 == w10 and w10s[k - 5] == w10s[k - 5] and sg * w10 >= sg * w10s[k - 5]
        raw = 0
        if raw1:
            raw = 1
            if (retest or div) and m20_turn:
                raw = 2
            if w5 == w5 and w10 == w10 and sg * w5 > sg * w10 and sg * x >= sg * w10 and w10_flat:
                raw = 3
        hold = lvl and (hold_mode == "low" or (hold_mode == "ma20" and sg * x >= sg * a20))
        lvl = max(lvl, raw) if hold else raw
        flags = {"retest": retest, "div": div, "m20_turn": bool(m20_turn), "w10_flat": bool(w10_flat)}
    info = {"move": move, "ext": E, "ext_date": d1, "since": since, **flags}
    if not top:                                      # 예전 이름도 유지(태그·추적 페이지에서 사용)
        info.update({"drop": move, "low": E, "low_date": d1})
    else:
        info.update({"rise": move, "high": E, "high_date": d1})
    return lvl, info


def bottom_stage(c, rs, r=None):
    """바닥 단계 0~3 (규칙은 _turn_stage 참고)"""
    return _turn_stage(c, rs, top=False)


def top_stage(c, rs, r=None):
    """꼭지 단계 0~3 (바닥 규칙을 위아래로 뒤집은 것)"""
    return _turn_stage(c, rs, top=True)


def bottom_stage_v1(c, rs, r):
    """[기존 규칙 · 비교용으로만 남김] 차트상 바닥 단계 0~3 (종가 기준). 하락 멈춤 20거래일 고정, 날마다 새로 판정(깜빡임 있음).
    전제: 최근 1년 고점 이후의 최저 종가(첫 저점)가 그 고점 대비 BOTTOM_DROP% 이상 빠졌고, 그 저점이 BOTTOM_WIN거래일 이내.
    1/3 하락 멈춤   = 첫 저점 뒤 BOTTOM_QUIET거래일 이상 신저가 없음 + 종가 ≥ 10일선
    2/3 바닥 다지기 = 1/3 + (쌍바닥: 저점에서 10% 이상 반등 → 반등폭 60% 이상 되돌려 첫 저점의 110% 이내로 다시 내려옴
                       또는 RSI 상승 다이버전스: 그 두 번째 저점이 첫 저점 105% 이내인데 RSI는 5 이상 높음) + 20일선 상승 전환
    3/3 추세 전환   = 1/3 조건 + 주봉 정배열(5주>10주) + 종가 ≥ 10주선 + 10주선 하락 멈춤(5거래일 전보다 같거나 높음)"""
    info = {}
    if len(c) < 260:
        return 0, info
    yr = c.iloc[-250:]
    dH, H = yr.idxmax(), float(yr.max())             # 최근 1년 고점
    after_h = c.loc[dH:]
    d1, L1 = after_h.idxmin(), float(after_h.min())  # 그 고점 이후의 최저 종가 = 첫 저점(창이 밀려도 바뀌지 않음)
    drop = (L1 / H - 1) * 100
    since = len(c.loc[d1:]) - 1
    if drop > -BOTTOM_DROP or since > BOTTOM_WIN:    # 충분히 안 빠졌거나, 저점이 너무 오래전이면 바닥 단계로 보지 않음
        return 0, info
    cl = float(c.iloc[-1])
    m10 = float(c.iloc[-10:].mean())
    m20 = c.rolling(20).mean()
    info = {"drop": drop, "low": L1, "low_date": d1, "since": since}
    quiet = since >= 20
    stage = 1 if (quiet and cl >= m10) else 0
    retest = div = False
    seg = c.loc[d1:]
    dp, P = seg.idxmax(), float(seg.max())         # 저점 뒤 반등 고점
    after = seg.loc[dp:].iloc[1:]
    if P >= L1 * 1.10 and len(after):               # 10% 이상 반등한 뒤에야 '두 번째 저점'을 봄
        d2, L2 = after.idxmin(), float(after.min())
        pulled = L2 <= P - (P - L1) * 0.6            # 반등폭의 60% 이상 되돌림
        retest = pulled and L2 <= L1 * 1.10          # 첫 저점 근처(110% 이내)까지 다시 내려옴 = 쌍바닥
        div = pulled and L2 <= L1 * 1.05 and float(rs.loc[d2]) > float(rs.loc[d1]) + 5
    m20_up = bool(m20.iloc[-1] > m20.iloc[-6])
    info.update({"retest": retest, "div": div, "m20_up": m20_up})
    if stage == 1 and (retest or div) and m20_up:
        stage = 2
    wk, w10s = r["week"], r["chart"]["w10"]
    w10 = w10s[-1]
    w10_up = w10 is not None and len(w10s) > 5 and w10s[-6] is not None and w10 >= w10s[-6]   # 10주선 하락 멈춤
    info["w10_up"] = bool(w10_up)
    if since >= 20 and cl >= m10 and wk.get("ok") and wk.get("above") and w10 is not None and cl >= w10 and w10_up:
        stage = 3
    return stage, info


# ---- 백테스트 후보 신호 3종 (2026-10-10, 리포트엔 아직 안 씀) ---------------------------------------
CLX_VOL = 2.5        # 거래량 클라이맥스: 고점 ±3일 안에 거래량이 20일 평균의 이 배수 이상
CLX_AFTER = (5, 10)  # 고점 뒤 이 거래일 범위 안에서 판정(신고가 없음 + 최근 5일 거래량이 평균 아래)
DIV_LOOK = (10, 60)  # 다이버전스: 첫 저점(고점)을 찾는 구간 = 오늘로부터 10~60거래일 전
DIV_RSI = 3          # 두 번째 저점의 RSI가 첫 저점보다 이만큼 이상 높아야(고점은 낮아야) 다이버전스
SR_SWING = 10        # 지지·저항: 앞뒤 10거래일 중 가장 낮은(높은) 종가 = 의미 있는 저점(고점)
SR_LOOK = (20, 250)  # 지지·저항 후보를 찾는 구간 = 오늘로부터 20~250거래일 전
SR_BAND = 3.0        # 지지·저항 '근처' = ±3%


def extra_signals(df):
    """날짜별 후보 신호(그날까지 데이터만 사용, 미래 누수 없음). 반환 DataFrame(bool 열):
    클라이맥스꼭지(▼) · RSI상승다이버전스(▲) · RSI하락다이버전스(▼) · 지지선반등(▲) · 저항선실패(▼) · 저항선돌파(▲)"""
    import numpy as np
    c = df["Close"].astype(float)
    n = len(c)
    cv = c.values
    rv = rsi(c).values
    out = {k: np.zeros(n, bool) for k in ("클라이맥스꼭지", "RSI상승다이버전스", "RSI하락다이버전스",
                                          "지지선반등", "저항선실패", "저항선돌파")}
    vr = None
    if "Volume" in df and df["Volume"].fillna(0).sum() > 0:
        v = df["Volume"].astype(float).replace(0, np.nan)
        vr = (v / v.shift(1).rolling(20, min_periods=10).mean()).values
    hi60 = c.rolling(60, min_periods=20).max().values
    # 의미 있는 저점·고점: 앞뒤 SR_SWING일 중 최저·최고 종가(뒤 10일이 지나야 확정 → t에서는 i ≤ t-SR_SWING 만 사용)
    w = 2 * SR_SWING + 1
    sw_lo = (c == c.rolling(w, center=True).min()).values
    sw_hi = (c == c.rolling(w, center=True).max()).values
    for t in range(70, n):
        x = cv[t]
        # 1) 거래량 클라이맥스 꼭지: 최근 10일 최고 종가가 60일 신고가였고, 그 무렵 거래량 폭증, 그 뒤 5~10일 신고가 없음 + 거래량 줄어듦
        if vr is not None:
            i = t - CLX_AFTER[1] + int(np.argmax(cv[t - CLX_AFTER[1]:t + 1]))
            if CLX_AFTER[0] <= t - i <= CLX_AFTER[1] and cv[i] >= hi60[i] and x < cv[i]:
                spike = np.nanmax(vr[max(i - 3, 0):min(i + 4, t + 1)])
                recent = np.nanmean(vr[t - 4:t + 1])
                out["클라이맥스꼭지"][t] = spike >= CLX_VOL and recent < 1.0
        # 2) RSI 다이버전스: 최근 5일 안의 저점이 10~60일 전 저점보다 낮은데 RSI는 더 높음(첫 저점 RSI는 과매도 35 이하), 오늘 반등
        a, b = t - DIV_LOOK[1], t - DIV_LOOK[0]
        j = t - 4 + int(np.argmin(cv[t - 4:t + 1]))           # 최근 저점
        i = a + int(np.argmin(cv[a:b]))                         # 앞 저점
        if cv[j] < cv[i] and rv[i] <= RSI_LO and rv[j] >= rv[i] + DIV_RSI and j < t and x > cv[j] and x > cv[t - 1]:
            out["RSI상승다이버전스"][t] = True
        j = t - 4 + int(np.argmax(cv[t - 4:t + 1]))
        i = a + int(np.argmax(cv[a:b]))
        if cv[j] > cv[i] and rv[i] >= RSI_HI and rv[j] <= rv[i] - DIV_RSI and j < t and x < cv[j] and x < cv[t - 1]:
            out["RSI하락다이버전스"][t] = True
        # 3) 지지·저항: 20~250일 전의 의미 있는 저점·고점 중 지금 가격에 가장 가까운 것
        a, b = max(t - SR_LOOK[1], 0), t - SR_LOOK[0] + 1
        lows = cv[a:b][sw_lo[a:b]]
        highs = cv[a:b][sw_hi[a:b]]
        lo5, hi5 = cv[t - 4:t + 1].min(), cv[t - 4:t + 1].max()
        band = SR_BAND / 100
        sup = lows[(lows <= x)]
        if len(sup):
            S = sup.max()                                        # 오늘 종가 아래 가장 가까운 지지선
            # 최근 5일 안에 지지선 ±3%까지 내려왔다가, 오늘 지지선 위에서 상승 마감
            out["지지선반등"][t] = abs(lo5 / S - 1) <= band and x > cv[t - 1] and x >= S
        res = highs[(highs >= x)]
        if len(res):
            R = res.min()                                        # 오늘 종가 위 가장 가까운 저항선
            out["저항선실패"][t] = abs(hi5 / R - 1) <= band and x < cv[t - 1] and x < R * (1 - band / 2)
        brk = highs[(highs < x)]
        if len(brk):
            R = brk.max()                                        # 오늘 처음으로 저항선을 2% 넘게 넘은 날
            out["저항선돌파"][t] = x >= R * 1.02 and cv[t - 1] < R * 1.02 and cv[t - 5:t].max() < R * 1.02
    return pd.DataFrame(out, index=c.index)


def long_up(r):
    """장기 추세 상승: 월봉 정배열(5월>10월) 또는 종가가 10월선 위"""
    mo = r["month"]
    m10 = r["chart"]["m10"][-1] if r.get("chart") else None
    return bool((mo.get("ok") and mo.get("above")) or (m10 is not None and r["close"] >= m10))


# ---------------------------------------------------------------- 종목 시그널
def stock_signals(r):
    """조건(싸다/과열) 2개 이상 + 트리거(크로스·RSI 방향전환) 1개 이상 → '타점', 조건만 충족 → '관심'"""
    d, dy = r["disp"], r["day"]
    recent = dy["ok"] and dy["cross"] and dy["cross"]["ago"] < CROSS_DAYS
    buy_c, buy_t, sell_c, sell_t = [], [], [], []
    bk, sk = {}, {}   # 강조용: 지표키 -> "c"(조건) / "t"(트리거)

    # RSI 기준은 추세에 따라 바뀜: 정·정·정이면 과열 80, 역·역·역이면 과매도 30 (강한 추세에선 RSI가 한쪽에 오래 머묾)
    arr = [r[k]["above"] if r[k]["ok"] else None for k in ("day", "week", "month")]
    trend_up, trend_dn = all(a is True for a in arr), all(a is False for a in arr)
    rsi_hi = RSI_HI_STRONG if trend_up else RSI_HI
    rsi_lo = RSI_LO_WEAK if trend_dn else RSI_LO
    if r["rsi"] <= rsi_lo:
        buy_c.append(f"RSI {r['rsi']:.0f} ({rsi_lo} 이하" + (", 역·역·역" if trend_dn else "") + ")"); bk["rsi"] = "c"
    if d and d["down"] <= NEAR_MIN:     # 종목마다 파도 크기가 달라 고정값 없이 '과거 최소 대비'로만 판단
        buy_c.append(f"50일 이격도 {d['cur']:.1f} (과거 최소 {d['min']:.1f}의 {d['down']:.0%})"); bk["disp"] = "c"
    if r["from_high"] <= -20:
        buy_c.append(f"52주 고점 대비 {r['from_high']:.0f}%"); bk["high"] = "c"
    if recent and dy["cross"]["golden"]:
        buy_t.append("5일 내 골든크로스"); bk["day"] = "t"
    if r["rsi_min5"] <= rsi_lo and r["rsi"] > r["rsi_min5"] and r["rsi"] >= r["rsi_prev"]:
        buy_t.append("RSI 저점 찍고 반등"); bk["rsi"] = "t"
    ma5 = r["chart"]["ma5"]
    ma5_up = ma5[-1] is not None and ma5[-2] is not None and ma5[-1] > ma5[-2]
    if dy["ok"] and not dy["above"] and dy["gap"] >= -CROSS_NEAR and ma5_up:
        buy_t.append(f"골든크로스 임박 (5일선 {dy['gap']:+.2f}%, 상승 중)"); bk["day"] = "t"
    falling = bool(r["ret5"] <= FALL_PCT or r.get("new_low3"))   # 아직 떨어지는 중이면 '싸 보여도' 보류

    if r["rsi"] >= rsi_hi:
        sell_c.append(f"RSI {r['rsi']:.0f} ({rsi_hi} 이상" + (", 정·정·정" if trend_up else "") + ")"); sk["rsi"] = "c"
    if d and d["up"] >= NEAR_MAX:       # 고정값(110) 없이 '과거 최대 대비'로만 판단
        sell_c.append(f"50일 이격도 {d['cur']:.1f} (과거 최대 {d['max']:.1f}의 {d['up']:.0%})"); sk["disp"] = "c"
    if r["ret5"] >= MOVE_PCT:
        sell_c.append(f"5일 {r['ret5']:+.1f}% 급등"); sk["ret5"] = "c"
    if recent and not dy["cross"]["golden"]:
        sell_t.append("5일 내 데드크로스"); sk["day"] = "t"
    if r["rsi_max5"] >= rsi_hi - 5 and r["rsi"] < r["rsi_max5"] and r["rsi"] <= r["rsi_prev"]:
        sell_t.append("RSI 고점 찍고 꺾임"); sk["rsi"] = "t"

    def level(c, t):
        return "타점" if (len(c) >= 2 and t) else ("관심" if len(c) >= 2 else None)

    # 추세 판단: 일봉·주봉이 모두 정배열이면 '상승 추세 유지'. 월봉은 수개월 지연되는 지표라
    # 장기 하락 뒤 급반등한 종목은 늘 역배열로 남으므로 판단에 쓰지 않고 참고로만 표시한다.
    wk = r["week"]
    strong_up = bool(dy["ok"] and dy["above"] and wk["ok"] and wk["above"])
    sell = level(sell_c, sell_t)
    if sell == "관심" and strong_up:
        sell = "과열"          # 과열이지만 추세는 살아 있음 → 매도 신호가 아니라 '이익 보호' 구간
    buy = level(buy_c, buy_t)
    if buy == "관심" and falling:
        # 싸 보이지만 아직 하락 중: 장기 추세가 상승이면 '눌림진행'(곧 매수 후보, 백테스트에서 기준선보다 나았음),
        # 장기 추세도 하락이면 '하락진행'(반등 확인 전까지 보류)
        buy = "눌림" if long_up(r) else "보류"
    # ---- 불타기 후보: 추세 유지 + 과열 아님 + 20일선 눌림 뒤 반등 확인 (셋 다 필요)
    ak = {}
    add, add_c, add_t = None, [], []
    ch = r["chart"]
    cl, m20, mm5 = ch["close"], ch["ma20"], ch["m5"]
    if len(cl) >= 10 and all(v is not None for v in m20[-10:]) and not sell and not falling:
        # 눌림 중에는 5일선이 10일선 아래로 내려가므로 일봉 정/역 대신
        # '주봉 정배열(5주>10주) + 20일선 상승 중(5거래일 전보다 높음) + 종가가 5월선 위'로 추세를 본다
        m20_up = m20[-1] > m20[-6]
        above_m5 = mm5[-1] is None or cl[-1] >= mm5[-1]
        trend = bool(wk["ok"] and wk["above"] and m20_up and above_m5)
        calm = r["rsi"] < rsi_hi and (not d or d["cur"] / d["max"] * 100 < ADD_DISP_MAX)
        gaps = [(cl[i] / m20[i] - 1) * 100 for i in range(-5, 0)]
        touched = min(gaps) <= ADD_TOUCH and gaps[-1] >= 0     # 닿았지만 종가는 20일선 위 유지
        bounce = cl[-1] > cl[-2] and gaps[-1] > min(gaps)
        if trend:
            add_c.append("주봉 정배열 · 20일선 상승 중" + (" · 5월선 위" if mm5[-1] is not None else "")); ak["week"] = "c"   # 추세 조건의 핵심은 주봉 정배열 → '주' 칸 강조
        if calm:
            add_c.append("과열 아님 (이격도·RSI)"); ak["disp"] = "c"
        if touched:
            add_c.append(f"20일선 눌림 (최근 5일 최저 {min(gaps):+.1f}%)"); ak["ma20"] = "c"
        if trend and calm and touched and bounce:
            add_t.append("20일선 지지 후 반등"); ak["ma20"] = "t"
            add = "후보"
    return {"buy": buy, "falling": falling, "add": add, "add_c": add_c, "add_t": add_t, "add_k": ak,
             "buy_c": buy_c, "buy_t": buy_t,
            "sell": sell, "sell_c": sell_c, "sell_t": sell_t, "strong_up": strong_up,
            "buy_k": bk, "sell_k": sk, "rsi_hi": rsi_hi, "rsi_lo": rsi_lo}


# ---------------------------------------------------------------- 분류
# 시그널 분류: (표시 이름, 신호 쪽, 내부 단계, 성격). 화면·칩·텍스트가 모두 이 이름과 순서를 쓴다.
# 성격: buy = 사는 쪽(세부내용 탭 연분홍), sell = 파는 쪽(연하늘), watch = 지켜보기(색 없음)
SIGNAL_GROUPS = (
    ("매수", "buy", "타점", "buy"),
    ("매도", "sell", "타점", "sell"),
    ("불타기", "add", "후보", "buy"),
    ("익절검토", "sell", "과열", "sell"),
    ("비중축소", "sell", "관심", "sell"),
    ("눌림진행", "buy", "눌림", "watch"),
    ("하락진행", "buy", "보류", "watch"),
    ("반등대기", "buy", "관심", "watch"),
)


def sig_groups(r):
    """이 종목이 속한 시그널 분류 [(이름, 성격), ...]"""
    return [(nm, tone) for nm, side, lv, tone in SIGNAL_GROUPS if r["sig"].get(side) == lv]


CROSS_WIN = {"day": 5, "week": 4, "month": 2}   # '최근 크로스'로 보는 기간: 일 5거래일, 주 4주, 월 2개월(이번 달·지난달)


def cross_count(r, golden, wins=None):
    """일·주·월 중 최근(CROSS_WIN 이내) 같은 방향(골든/데드) 크로스가 난 단위 수. wins로 기간을 바꿔 셀 수 있음(백테스트 비교용)"""
    n = 0
    for k, win in (wins or CROSS_WIN).items():
        st = r[k]
        cr = st.get("cross") if st.get("ok") else None
        if cr and cr["golden"] == golden and cr["ago"] < win:
            n += 1
    return n


def flags(r):
    f = []
    d = r["day"]
    for golden, nm in ((True, "골든"), (False, "데드")):
        k = cross_count(r, golden)
        if k >= 2:
            f.append(f"{nm}X{k}")
        elif d["ok"] and d["cross"] and d["cross"]["golden"] == golden and d["cross"]["ago"] < CROSS_DAYS:
            f.append(f"최근{nm}")
    if abs(r["ret5"]) >= MOVE_PCT or abs(r["maxday"]) >= MOVE_PCT:
        f.append(f"{MOVE_PCT:g}%↑변동")
    if r["disp"] and r["disp"]["up"] >= NEAR_MAX:
        f.append("이격도상단")
    if r["disp"] and r["disp"]["down"] <= NEAR_MIN:
        f.append("이격도하단")
    if r.get("bottom"):
        f.append(f"바닥{r['bottom']}/3")
    if r.get("top"):
        f.append(f"꼭지{r['top']}/3")
    return [nm for nm, _ in sig_groups(r)] + f


def fd(date, unit):
    return f"{date:%y.%m}" if unit == "월" else f"{date:%y.%m.%d}"


def trans_str(label, st, unit):
    if not st["ok"]:
        return f"{label} 자료부족"
    cr = st["cross"]
    if not cr:
        return f"{label} 전환없음"
    return f"{label}{'▲' if cr['golden'] else '▼'}{fd(cr['date'], unit)}({cr['ago']}{unit})"


def trans_line(r):
    return "    " + " ".join([trans_str("일", r["day"], "일"), trans_str("주", r["week"], "주"),
                              trans_str("월", r["month"], "월")])


def tl(r):
    return f"{r['name']} ({r['code']})  RSI {r['rsi']:.0f}"


def title(r):
    return f"{r['name']} ({r['code']})"


# ---------------------------------------------------------------- 1. 시장 지표
def _yf_close(sym):
    import yfinance as yf
    return yf.Ticker(sym).history(period="2mo")["Close"].dropna()


INDEXES = (("kospi", "코스피", "KS11", "^KS11"), ("kosdaq", "코스닥", "KQ11", "^KQ11"),
           ("spx", "S&P500", "US500", "^GSPC"), ("ndx", "나스닥", "IXIC", "^IXIC"))


def _index_close(fdr_code, yf_code):
    """지수 일봉 종가: FinanceDataReader 먼저, 실패하면 yfinance"""
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s = fdr.DataReader(fdr_code, START)["Close"].dropna()
        if len(s) > 30:
            return s
    except Exception:
        pass
    import yfinance as yf
    s = yf.Ticker(yf_code).history(start=START)["Close"].dropna()
    s.index = s.index.tz_localize(None)
    return s


def _index_info(name, s):
    disp = (s / s.rolling(50).mean() * 100).dropna()
    d = None
    if len(disp):     # 종목과 같은 형식(disp_view로 '최대/최소 대비 %' 표시)
        cur, mx, mn = float(disp.iloc[-1]), float(disp.max()), float(disp.min())
        d = {"cur": cur, "max": mx, "min": mn, "up": cur / mx, "down": cur / mn}
    return {"name": name, "close": float(s.iloc[-1]), "chg": float((s.iloc[-1] / s.iloc[-2] - 1) * 100),
            "date": s.index[-1], "chart": chart_data(s), "disp": d, "rsi": float(rsi(s).iloc[-1])}


CREDIT_YEARS = 5      # 금융투자협회 통계를 몇 년치 받을지(백분위의 '과거' 범위)
CREDIT_TOP = 90       # 신용잔고/예탁금 비율이 과거 이 백분위 이상이면 위험 지표 켜짐(= 과거 상위 10%)
PBR_LINK = "https://www.indexergo.com/series/?frq=D&idxDetail=20406"   # 코스피200 PBR(직접 확인용 링크). 자동 수집은 안 함(KRX 로그인 필요·사이트가 수집 차단)


def _freesis_rows(obj, years=CREDIT_YEARS):
    """금융투자협회 통계(freesis) 표를 1년씩 받아 행 목록으로. obj = 표 이름(OBJ_NM)"""
    import requests
    url = "https://freesis.kofia.or.kr/meta/getMetaDataList.do"
    hdr = {"User-Agent": "Mozilla/5.0", "Content-Type": "application/json; charset=UTF-8",
           "Referer": "https://freesis.kofia.or.kr/stat/FreeSIS.do"}
    end = datetime.today()
    rows, diag = [], ""
    for k in range(years):
        e = end - timedelta(days=365 * k)
        st = e - timedelta(days=364)
        body = {"dmSearch": {"tmpV40": "1000000", "tmpV41": "1", "tmpV1": "D", "tmpV45": f"{st:%Y%m%d}",
                             "tmpV46": f"{e:%Y%m%d}", "OBJ_NM": obj}}
        resp = requests.post(url, json=body, headers=hdr, timeout=15)
        try:
            ds = resp.json().get("ds1") or []
        except Exception:
            ds = []
        if k == 0:
            diag = f"HTTP {resp.status_code} · 행 {len(ds)}개 · 첫 행 {str(ds[0])[:160] if ds else resp.text[:120]!r}"
        rows += ds
        if not ds:
            break
    if len(rows) < 20:
        raise ValueError(f"{len(rows)}일치만 읽음 — {diag}")
    return rows, diag


def _num(v):
    try:
        return float(str(v).replace(",", ""))
    except Exception:
        return None


def _fs_keys(row):
    return sorted([k for k in row if k.upper().startswith("TMPV") and k[4:].isdigit() and k.upper() != "TMPV1"],
                  key=lambda k: int(k[4:]))


def _fs_series(rows, key):
    data = {}
    for r in rows:
        d = str(r.get("TMPV1", r.get("tmpV1", ""))).replace("-", "").replace(".", "").replace("/", "")[:8]
        v = _num(r.get(key))
        if len(d) == 8 and d.isdigit() and v:
            data[pd.Timestamp(d)] = v
    return pd.Series(data, dtype=float).sort_index()


def credit_tables():
    """신용공여 잔고(전체·코스피 신용거래융자)와 고객예탁금. 단위 억원. 반환 (DataFrame[cred, cred_ks], dep Series 또는 예외)
    신용 표(STATSCU0100000070BO)는 열 이름이 일련번호라 첫 행에서 '전체 = 유가증권 + 코스닥'인 첫 세 칸을 찾음.
    예탁금 표(증시자금추이, STATSCU0100000060BO)는 첫 숫자 열 = 고객예탁금으로 보고, 신용보다 커야 통과"""
    rows, diag = _freesis_rows("STATSCU0100000070BO")
    keys = _fs_keys(rows[0])
    first = [_num(rows[0].get(k)) for k in keys]
    trip = None
    for i in range(len(keys) - 2):
        a, b, c = first[i:i + 3]
        if a and b and c and abs(a - (b + c)) <= a * 0.005:
            trip = keys[i:i + 3]
            break
    if trip is None:
        raise ValueError(f"신용 표에서 전체=유가+코스닥 열을 못 찾음 — {diag}")
    tot, ks = _fs_series(rows, trip[0]), _fs_series(rows, trip[1])
    div = 100 if tot.median() > 3e6 else 1            # 백만원 → 억원
    cred = pd.DataFrame({"cred": tot / div, "cred_ks": ks / div}).dropna()
    try:
        drows, ddiag = _freesis_rows("STATSCU0100000060BO")
        dk = _fs_keys(drows[0])
        dep = _fs_series(drows, dk[0]) / div
        both = dep.reindex(cred.index).dropna()
        ratio = (cred["cred"].reindex(both.index) / both * 100).median() if len(both) else float("nan")
        if not 3 < ratio < 100:
            raise ValueError(f"신용/예탁금 비율 이상 {ratio:.0f}% (예탁금 열 {dk[0]}) — {ddiag}")
    except Exception as e:
        dep = e
    return cred, dep


def credit_series(cred, dep):
    df = pd.DataFrame({"dep": dep, "cred": cred["cred"]}).dropna()
    if len(df) < 20:
        raise ValueError(f"예탁금·신용 날짜가 겹치는 날 {len(df)}일뿐")
    df["ratio"] = df["cred"] / df["dep"] * 100
    return df


def credit_info(df):
    r = df["ratio"]
    cur = float(r.iloc[-1])
    return {"ratio": cur, "date": r.index[-1], "dep": float(df["dep"].iloc[-1]), "cred": float(df["cred"].iloc[-1]),
            "pct": float((r <= cur).mean() * 100),           # 과거(읽은 기간) 중 오늘보다 낮거나 같은 날 비율
            "max": float(r.max()), "min": float(r.min()), "since": r.index[0], "n": len(r),
            "chg20": float(cur - r.iloc[-21]) if len(r) > 20 else None,
            "spark": [round(float(x), 2) for x in r.tail(120)]}


def _pctl(r):
    cur = float(r.iloc[-1])
    return float((r <= cur).mean() * 100)


def _series_info(r, spark_n=120):
    """시계열 공통 요약: 현재·과거 백분위·최고·최저·20일 변화·추이선"""
    cur = float(r.iloc[-1])
    return {"cur": cur, "date": r.index[-1], "pct": _pctl(r), "max": float(r.max()), "min": float(r.min()),
            "since": r.index[0], "n": len(r), "chg20": float(cur - r.iloc[-21]) if len(r) > 20 else None,
            "spark": [round(float(x), 3) for x in r.tail(spark_n)]}


def market_snapshot():
    snap = {"ks": None, "vix": None, "y10": None, "y30": None, "idx": {}, "err": []}
    for key, nm, fc, yc in INDEXES:         # 코스피·코스닥·S&P500·나스닥 (카드 + 차트용)
        try:
            snap["idx"][key] = _index_info(nm, _index_close(fc, yc))
        except Exception as e:
            snap["err"].append(f"{nm} ({type(e).__name__})")
    try:
        ks = fdr.DataReader("KS11", START)["Close"].dropna()
        disp = (ks / ks.rolling(50).mean() * 100).dropna()
        hi250 = ks.rolling(250, min_periods=1).max().iloc[-1]
        win = ks.tail(250)
        days_since_low = len(win.loc[win.idxmin():]) - 1   # 52주 최저 종가가 몇 거래일 전인지
        snap["ks"] = {"close": float(ks.iloc[-1]), "chg": float((ks.iloc[-1] / ks.iloc[-2] - 1) * 100),
                      "date": ks.index[-1], "disp": float(disp.iloc[-1]), "disp_max": float(disp.max()),
                      "disp_min": float(disp.min()), "rsi": float(rsi(ks).iloc[-1]),
                      "from_high": float((ks.iloc[-1] / hi250 - 1) * 100), "cross": cross_state(ks),
                      "days_since_low": int(days_since_low), "chart": chart_data(ks)}
    except Exception as e:
        snap["err"].append(f"코스피 ({type(e).__name__})")
    for key, sym, nm in (("vix", "^VIX", "VIX"), ("y10", "^TNX", "미국채 10년"), ("y30", "^TYX", "미국채 30년")):
        try:
            h = _yf_close(sym)
            n = len(h)
            snap[key] = {"last": float(h.iloc[-1]), "chg": float(h.iloc[-1] - h.iloc[-2]), "date": h.index[-1],
                         "chg10": float(h.iloc[-1] - h.iloc[-11]) if n >= 11 else None,
                         "pct10": float((h.iloc[-1] / h.iloc[-11] - 1) * 100) if n >= 11 else None}
        except Exception as e:
            snap["err"].append(f"{nm} ({type(e).__name__})")
    snap["credit"] = snap["credit_ks"] = None
    try:
        cred, dep = credit_tables()
        snap["credit_ks"] = _series_info(cred["cred_ks"])
        if isinstance(dep, Exception):
            snap["err"].append(f"고객예탁금 ({type(dep).__name__}: {str(dep)[:220]})")
        else:
            snap["credit"] = credit_info(credit_series(cred, dep))
    except Exception as e:
        snap["err"].append(f"신용잔고 ({type(e).__name__}: {str(e)[:220]})")
    return snap


def _level(score, total, kind):
    ratio = score / total if total else 0
    hi, mid = ratio >= 0.6, ratio >= 0.3      # 5개면 3개 이상 / 2개, 6개면 4개 이상 / 2개
    if kind == "risk":
        return ("경고", "lv2") if hi else ("주의", "lv1") if mid else ("낮음", "lv0")
    return ("바닥권 신호 다수", "lv2") if hi else ("바닥 형성 가능성", "lv1") if mid else ("신호 약함", "lv0")


def market_signals(snap, results):
    """시장 위험 / 시장 바닥 체크리스트. 항목 = (설명, 현재값, True|False|None(데이터없음))"""
    ks, vix, y10, cr = snap.get("ks"), snap.get("vix"), snap.get("y10"), snap.get("credit")
    n = len(results)
    bear = sum(1 for r in results if r["day"]["ok"] and not r["day"]["above"]) / n if n else None
    weak = sum(1 for r in results if r["rsi"] <= 35) / n if n else None

    risk = [
        ("코스피 50일 이격도 120 이상 (과열)", f"{ks['disp']:.1f}" if ks else "-", ks["disp"] >= 120 if ks else None),
        ("코스피 RSI 70 이상", f"{ks['rsi']:.0f}" if ks else "-", ks["rsi"] >= 70 if ks else None),
        ("VIX 25 이상 또는 10일간 30% 이상 급등", f"{vix['last']:.1f}" if vix else "-",
         (vix["last"] >= 25 or (vix["pct10"] is not None and vix["pct10"] >= 30)) if vix else None),
        ("미국 10년물 금리 10일간 0.3%p 이상 상승", f"{y10['chg10']:+.2f}%p" if y10 and y10["chg10"] is not None else "-",
         (y10["chg10"] >= 0.3) if y10 and y10["chg10"] is not None else None),
        ("내 종목 70% 이상이 일봉 역배열", f"{bear:.0%}" if bear is not None else "-",
         (bear >= 0.7) if bear is not None else None),
        (f"신용잔고/예탁금 비율 과거 상위 {100 - CREDIT_TOP}% (빚투 과열)",
         f"{cr['ratio']:.1f}% · 상위 {max(100 - cr['pct'], 0):.0f}%" if cr else "-",
         (cr["pct"] >= CREDIT_TOP) if cr else None),
    ]
    bottom = [
        ("코스피 50일 이격도 90 이하 또는 과거 최소권", f"{ks['disp']:.1f}" if ks else "-",
         (ks["disp"] <= 90 or ks["disp"] <= ks["disp_min"] * NEAR_MIN) if ks else None),
        ("코스피 RSI 35 이하", f"{ks['rsi']:.0f}" if ks else "-", ks["rsi"] <= 35 if ks else None),
        ("코스피 52주 고점 대비 -20% 이하 + 최근 20거래일 신저점 없음",
         f"{ks['from_high']:.1f}% · 저점 {ks['days_since_low']}일 전" if ks else "-",
         (ks["from_high"] <= -20 and ks["days_since_low"] >= 20) if ks else None),
        ("VIX 30 이상 (공포 극단)", f"{vix['last']:.1f}" if vix else "-", vix["last"] >= 30 if vix else None),
        ("내 종목 30% 이상이 RSI 35 이하", f"{weak:.0%}" if weak is not None else "-",
         (weak >= 0.3) if weak is not None else None),
    ]
    out = {}
    for key, items in (("risk", risk), ("bottom", bottom)):
        score = sum(1 for _, _, st in items if st)
        total = sum(1 for _, _, st in items if st is not None)
        label, cls = _level(score, len(items), key)   # 데이터가 없는 항목도 분모에 포함(과대평가 방지)
        out[key] = {"items": items, "score": score, "total": total, "label": label, "cls": cls}
    return out


def market_text(snap):
    L = ["■ 1. 시장 지표"]
    ks = snap.get("ks")
    if ks:
        L.append(f"코스피 {ks['close']:,.2f} ({ks['chg']:+.2f}%, {ks['date']:%m-%d})")
        L.append(f"  50일 이격도 {ks['disp']:.1f} (과거 최대 {ks['disp_max']:.1f} / 최소 {ks['disp_min']:.1f}) · RSI {ks['rsi']:.0f}"
                 f" · 52주 고점 대비 {ks['from_high']:+.1f}%")
    for key in ("kosdaq",):
        v = (snap.get("idx") or {}).get(key)
        if v:
            L.append(f"{v['name']} {v['close']:,.2f} ({v['chg']:+.2f}%, {v['date']:%m-%d})")
    for key, nm, unit in (("vix", "미국 VIX", ""), ("y10", "미국채 10년물", "%"), ("y30", "미국채 30년물", "%")):
        v = snap.get(key)
        if v:
            L.append(f"{nm} {v['last']:.2f}{unit} (전일 대비 {v['chg']:+.2f}, {v['date']:%m-%d})")
    cr = snap.get("credit")
    if cr:
        L.append(f"신용잔고/고객예탁금 {cr['ratio']:.1f}% (신용 {cr['cred'] / 1e4:,.1f}조 / 예탁금 {cr['dep'] / 1e4:,.1f}조, {cr['date']:%m-%d})"
                 f" · {cr['since']:%Y-%m} 이후 상위 {max(100 - cr['pct'], 0):.0f}%")
    ck = snap.get("credit_ks")
    if ck:
        L.append(f"코스피 신용잔고 {ck['cur'] / 1e4:,.1f}조 ({ck['date']:%m-%d}) · {ck['since']:%Y-%m} 이후 상위 {max(100 - ck['pct'], 0):.0f}%")
    for e in snap.get("err", []):
        L.append(f"가져오기 실패: {e}")
    return "\n".join(L)


def _n(x):
    t = f"{x:.1f}"
    return t[:-2] if t.endswith(".0") else t


def disp_view(d):
    """50일 이격도의 위치: 과거 범위에서 높은 쪽이면 (up, 최대 대비 %), 낮은 쪽이면 (down, 최소 대비 %)"""
    rng = d["max"] - d["min"]
    pos = (d["cur"] - d["min"]) / rng if rng > 0 else 0.5
    if pos >= 0.5:
        return {"side": "high", "pct": d["cur"] / d["max"] * 100, "strong": d["up"] >= NEAR_MAX}
    return {"side": "low", "pct": d["cur"] / d["min"] * 100, "strong": d["down"] <= NEAR_MIN}


def extra_line(r, lv=None):
    """한 줄 요약: 50일 이격 현재 (최소/최대) 일/주/월 배열 · 10일선 대비"""
    d = r["disp"]
    parts = []
    if d:
        v = disp_view(d)
        parts.append(f"50일 이격 {'상단' if v['side'] == 'high' else '하단'} {v['pct']:.0f}% ({_n(d['min'])}/{_n(d['max'])})")
    st = []
    for lb, k in (("일", "day"), ("주", "week"), ("월", "month")):
        x = r[k]
        st.append(f"{lb}{'정' if x['above'] else '역'}" if x["ok"] else f"{lb}-")
    parts.append("/".join(st))
    g = (r["close"] / r["ma10"] - 1) * 100
    parts.append(f"10일 {abs(g):.1f}%{'▲' if g >= 0 else '▼'}" + (f" (이탈선 {fmt_price(r['ma10'])})" if lv == "과열" else ""))
    return " · ".join(parts)


def signals_text(results, msig):
    L = ["■ 0. 오늘의 시그널 (참고용 규칙 기반 신호)"]
    for key, nm in (("risk", "시장 위험"), ("bottom", "시장 바닥")):
        g = msig[key]
        on = [lb for lb, _, st in g["items"] if st]
        L.append(f"{nm}: {g['score']}/{g['total']} [{g['label']}]" + (" - " + " / ".join(on) if on else ""))
    for head, side, lv, _ in SIGNAL_GROUPS:
        hit = [r for r in results if r["sig"][side] == lv]
        if not hit:            # 해당 종목이 없는 분류는 생략
            continue
        L.append(f"\n▶ {head} [{len(hit)}]")
        for r in hit:
            sg = r["sig"]
            L.append(f"• {tl(r)}")
            L.append("    조건: " + ", ".join(sg[side + "_c"]) + (" | 트리거: " + ", ".join(sg[side + "_t"]) if sg[side + "_t"] else ""))
            L.append("    " + extra_line(r, lv))
    return "\n".join(L)


# ---------------------------------------------------------------- 2. 스크리닝
def section(head, items, lines_fn, tone=""):
    """(제목, 종목수, 줄목록, 색) — 색: buy=분홍, sell=하늘, hot=노랑, ''=없음 (종목이 있을 때만 칠함)"""
    lines = []
    for r in items:
        lines.extend(lines_fn(r))
    return (head, len(items), lines, tone)


def summary_sections(results):
    """스크리닝 결과를 [(제목, 종목수, 줄목록), ...] 로 반환"""
    S = []

    # 일·주·월 중 몇 개에서 최근 같은 방향 크로스가 났는지 (X3 = 셋 다, X2 = 둘)
    def xsec(golden, k):
        hit = [r for r in results if cross_count(r, golden) == k]
        nm = "골든" if golden else "데드"
        return section(f"{nm}크로스 X{k}  (최근: 일 {CROSS_WIN['day']}거래일 · 주 {CROSS_WIN['week']}주 · 월 {CROSS_WIN['month']}개월 이내)",
                       hit, lambda r: [f"• {tl(r)}", trans_line(r)], "buy" if golden else "sell")

    for golden in (True, False):
        for k in (3, 2):
            S.append(xsec(golden, k))

    # 과열 종목: 과열 조건(RSI·이격도·5일 급등) 2개 이상 — 매도/익절검토/비중축소 어디로 분류됐든 모아서 표시
    hot = [r for r in results if len(r["sig"]["sell_c"]) >= 2]
    grp = lambda r: next((nm for nm, side, lv, _ in SIGNAL_GROUPS if side == "sell" and r["sig"]["sell"] == lv), "")
    S.append(section("과열 종목 (과열 조건 2개 이상)", hot,
                     lambda r: [f"• {tl(r)}" + (f"  → {grp(r)}" if grp(r) else ""),
                                "    " + ", ".join(r["sig"]["sell_c"])], "hot"))

    # 5일간 큰 변동
    mv = sorted([r for r in results if abs(r["ret5"]) >= MOVE_PCT or abs(r["maxday"]) >= MOVE_PCT],
                key=lambda r: -abs(r["ret5"]))
    S.append(section(f"최근 5일간 {MOVE_PCT:g}% 이상 변동 (5일 누적 또는 하루 기준)", mv,
                     lambda r: [f"• {tl(r)}", f"    5일 {r['ret5']:+.1f}% (하루 최대 {r['maxday']:+.1f}%)"]))

    # 50일 이격도 최대/최소 접근
    def note(r):
        return f" ※자료 {r['disp']['months']:.0f}개월뿐" if r["disp"]["months"] < 12 else ""

    hi = sorted([r for r in results if r["disp"] and r["disp"]["up"] >= NEAR_MAX], key=lambda r: -r["disp"]["up"])
    lo = sorted([r for r in results if r["disp"] and r["disp"]["down"] <= NEAR_MIN], key=lambda r: r["disp"]["down"])
    S.append(section(f"50일 이격도가 과거 최대 × {NEAR_MAX:g} 이상", hi,
                     lambda r: [f"• {tl(r)}",
                                f"    현재 {r['disp']['cur']:.1f} ≥ 기준 {r['disp']['max_thr']:.1f}"
                                f" (과거 최대 {r['disp']['max']:.1f}, {r['disp']['max_date']:%y.%m.%d}){note(r)}"], "sell"))
    S.append(section(f"50일 이격도가 과거 최소 × {NEAR_MIN:g} 이하", lo,
                     lambda r: [f"• {tl(r)}",
                                f"    현재 {r['disp']['cur']:.1f} ≤ 기준 {r['disp']['min_thr']:.1f}"
                                f" (과거 최소 {r['disp']['min']:.1f}, {r['disp']['min_date']:%y.%m.%d}){note(r)}"], "buy"))
    return S


def summary_text(results):
    L = ["■ 2. 종목 스크리닝", "표기: 일/주/월 = 5선·10선 기준, ▲역→정배열(골든) ▼정→역배열(데드), 괄호는 전환 후 경과"]
    for head, n, lines, _ in summary_sections(results):
        L.append(f"\n▶ {head}  [{n}]")
        L.extend(lines if n else ["  해당 종목 없음"])
    return "\n".join(L)


# ---------------------------------------------------------------- 3. 종목별 세부
def cross_detail(label, st, unit):
    if not st["ok"]:
        return f"  {label}: 데이터 부족 (자료 {st['n']}{unit}치뿐이라 10{unit}선 계산 불가)"
    pos = "정배열" if st["above"] else "역배열"
    s = f"  {label}: 5선/10선 이격 {st['gap']:+.2f}% ({pos})"
    cr = st["cross"]
    if cr:
        kind = "역→정 ▲골든크로스" if cr["golden"] else "정→역 ▼데드크로스"
        dt = f"{cr['date']:%Y-%m}" if unit == "월" else f"{cr['date']:%Y-%m-%d}"
        s += f"\n       최근 전환: {kind} {dt} ({cr['ago']}{unit} 전)"
    return s


def detail_text(r):
    tag = flags(r)
    L = [f"===== {title(r)}  기준일 {r['last']:%Y-%m-%d}, 종가 {fmt_price(r['close'])} =====" + (f"  [{' / '.join(tag)}]" if tag else ""),
         f"RSI(14): {r['rsi']:.1f}" + ("  (과매수권)" if r["rsi"] >= 70 else "  (과매도권)" if r["rsi"] <= 30 else ""),
         cross_detail("일봉", r["day"], "일"), cross_detail("주봉", r["week"], "주"), cross_detail("월봉", r["month"], "월")]
    d = r["disp"]
    if d:
        L.append(f"50일 이격도: 현재 {d['cur']:.1f} (과거 분포 하위 {d['pct']:.0f}%)")
        L.append(f"  최대 {d['max']:.1f} ({d['max_date']:%Y-%m-%d}) / 최소 {d['min']:.1f} ({d['min_date']:%Y-%m-%d})")
        L.append(f"  현재는 최대의 {d['up']:.0%} (기준 {d['max_thr']:.1f}) / 최소의 {d['down']:.0%} (기준 {d['min_thr']:.1f})  (자료 {d['since']:%Y-%m-%d}~)")
    L.append(f"52주 최고가(장중): {fmt_price(r['high52'])} → 현재 {r['from_high']:+.1f}%")
    L.append(f"최근 5거래일 등락률: {r['ret5']:+.2f}%  (일별 " + ", ".join(f"{x:+.1f}%" for x in r["rets"]) + ")")
    return "\n".join(L)


def detail_section(results):
    return "■ 3. 종목별 세부내용 (플래그가 붙은 종목은 [ ] 안에 표시)\n\n" + "\n\n".join(detail_text(r) for r in results)


if __name__ == "__main__":
    codes = sys.argv[1:] or ["005930"]
    res = []
    for cd in codes:
        try:
            res.append(analyze(cd, cd))
        except Exception as e:
            print(f"{cd}: 실패 ({type(e).__name__}: {e})")
    snap = market_snapshot()
    print(market_text(snap), "\n")
    print(signals_text(res, market_signals(snap, res)), "\n")
    print(summary_text(res), "\n")
    print(detail_section(res))
