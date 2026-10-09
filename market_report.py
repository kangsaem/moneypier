"""
장 마감 리포트: 1) 시장 지표  2) 종목 스크리닝  3) 종목별 세부내용
설치: pip install finance-datareader yfinance pandas requests
사용: python market_report.py 005930 000660 AAPL
"""
import contextlib
import io
import sys
from datetime import datetime, timedelta

import pandas as pd
import FinanceDataReader as fdr

START = (datetime.today() - timedelta(days=365 * 12)).strftime("%Y-%m-%d")

# ===== 스크리닝 기준 (여기 숫자만 바꾸면 됩니다) =====
CROSS_DAYS = 5      # '최근 N거래일 이내' 골든/데드크로스
MOVE_PCT = 8.0      # 최근 5거래일 누적 등락 또는 하루 등락이 이 % 이상이면 포함
NEAR_RATIO = 0.9    # 50일 이격도가 과거 최대/최소 범위의 이 비율(90%) 이상 접근하면 포함


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


def analyze(code, name):
    """종목 하나의 모든 지표를 계산해 dict로 반환"""
    df = load_prices(code)
    c = df["Close"].dropna()
    if len(c) < 30:
        raise ValueError("시세 데이터가 부족하거나 조회 실패")
    r = {"code": code, "name": name, "last": c.index[-1], "close": float(c.iloc[-1]),
         "rsi": float(rsi(c).iloc[-1]),
         "day": cross_state(c), "week": cross_state(make_bars(c, "W-FRI")),
         "month": cross_state(make_bars(c, "ME"))}

    disp = (c / c.rolling(50).mean() * 100).dropna()
    r["disp"] = None
    if len(disp):
        cur, mx, mn = float(disp.iloc[-1]), float(disp.max()), float(disp.min())
        # 이격도는 100이 기준선이므로, 100에서 최대/최소까지 거리 중 얼마나 왔는지로 접근도를 계산
        up = (cur - 100) / (mx - 100) if (mx > 100 and cur > 100) else 0.0
        dn = (100 - cur) / (100 - mn) if (mn < 100 and cur < 100) else 0.0
        r["disp"] = {"cur": cur, "max": mx, "max_date": disp.idxmax(), "min": mn, "min_date": disp.idxmin(),
                     "up": up, "down": dn, "pct": float((disp < cur).mean() * 100),
                     "months": len(disp) / 21, "since": disp.index[0]}

    high = df["High"].dropna() if "High" in df else c
    h52 = float(high.rolling(250, min_periods=1).max().iloc[-1])
    r["high52"], r["from_high"] = h52, (r["close"] / h52 - 1) * 100

    rets = (c.pct_change().tail(5) * 100).tolist()
    r["rets"] = rets
    r["ret5"] = float((c.iloc[-1] / c.iloc[-6] - 1) * 100) if len(c) >= 6 else 0.0
    r["maxday"] = max(rets, key=abs) if rets else 0.0
    return r


# ---------------------------------------------------------------- 분류
def flags(r):
    f = []
    d = r["day"]
    if d["ok"] and d["cross"] and d["cross"]["ago"] < CROSS_DAYS:
        f.append("최근골든" if d["cross"]["golden"] else "최근데드")
    if abs(r["ret5"]) >= MOVE_PCT or abs(r["maxday"]) >= MOVE_PCT:
        f.append(f"{MOVE_PCT:g}%↑변동")
    if r["disp"] and r["disp"]["up"] >= NEAR_RATIO:
        f.append("이격도상단")
    if r["disp"] and r["disp"]["down"] >= NEAR_RATIO:
        f.append("이격도하단")
    return f


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


def title(r):
    return f"{r['name']} ({r['code']})"


# ---------------------------------------------------------------- 1. 시장 지표
def market_text():
    L = ["■ 1. 시장 지표"]
    try:
        ks = fdr.DataReader("KS11", START)["Close"].dropna()
        disp = (ks / ks.rolling(50).mean() * 100).dropna()
        chg = (ks.iloc[-1] / ks.iloc[-2] - 1) * 100
        L.append(f"코스피 {ks.iloc[-1]:,.2f} ({chg:+.2f}%, {ks.index[-1]:%m-%d})")
        L.append(f"  50일 이격도 {disp.iloc[-1]:.1f} (과거 최대 {disp.max():.1f} / 최소 {disp.min():.1f})")
    except Exception as e:
        L.append(f"코스피: 가져오기 실패 ({type(e).__name__})")

    try:
        import yfinance as yf
        for sym, nm, unit in (("^VIX", "미국 VIX", ""), ("^TNX", "미국채 10년물", "%"), ("^TYX", "미국채 30년물", "%")):
            try:
                h = yf.Ticker(sym).history(period="10d")["Close"].dropna()
                L.append(f"{nm} {h.iloc[-1]:.2f}{unit} (전일 대비 {h.iloc[-1] - h.iloc[-2]:+.2f}, {h.index[-1]:%m-%d})")
            except Exception as e:
                L.append(f"{nm}: 가져오기 실패 ({type(e).__name__})")
    except Exception as e:
        L.append(f"미국 지표: 가져오기 실패 ({type(e).__name__})")
    return "\n".join(L)


# ---------------------------------------------------------------- 2. 스크리닝
def section(head, items, lines_fn):
    out = [f"\n▶ {head}"]
    if not items:
        out.append("  해당 종목 없음")
    for r in items:
        out.extend(lines_fn(r))
    return out


def summary_text(results):
    L = ["■ 2. 종목 스크리닝", "표기: 일/주/월 = 5선·10선 기준, ▲역→정배열(골든) ▼정→역배열(데드), 괄호는 전환 후 경과"]

    def ago_key(r):
        cr = r["day"]["cross"]
        return cr["ago"] if cr else 10 ** 6

    # 2-1, 2-2: 현재 일봉 배열 상태별로 '언제 바뀌었는지'
    bear = sorted([r for r in results if r["day"]["ok"] and not r["day"]["above"]], key=ago_key)
    bull = sorted([r for r in results if r["day"]["ok"] and r["day"]["above"]], key=ago_key)
    L += section("정배열 → 역배열로 바뀐 종목 (현재 일봉 역배열, 최근 전환순)", bear,
                 lambda r: [f"• {title(r)}", trans_line(r)])
    L += section("역배열 → 정배열로 바뀐 종목 (현재 일봉 정배열, 최근 전환순)", bull,
                 lambda r: [f"• {title(r)}", trans_line(r)])

    # 2-3, 2-4: 최근 N일 크로스
    def recent(golden):
        rs = [r for r in results if r["day"]["ok"] and r["day"]["cross"]
              and r["day"]["cross"]["golden"] == golden and r["day"]["cross"]["ago"] < CROSS_DAYS]
        return sorted(rs, key=ago_key)

    def cross_lines(r):
        cr = r["day"]["cross"]
        when = "최근 거래일" if cr["ago"] == 0 else f"{cr['ago']}거래일 전"
        return [f"• {title(r)}  {cr['date']:%m-%d} ({when}), 현재 이격 {r['day']['gap']:+.2f}%"]

    L += section(f"최근 {CROSS_DAYS}일간 골든크로스 (5일선이 10일선 상향돌파)", recent(True), cross_lines)
    L += section(f"최근 {CROSS_DAYS}일간 데드크로스 (5일선이 10일선 하향돌파)", recent(False), cross_lines)

    # 2-5: 5일간 큰 변동
    mv = sorted([r for r in results if abs(r["ret5"]) >= MOVE_PCT or abs(r["maxday"]) >= MOVE_PCT],
                key=lambda r: -abs(r["ret5"]))
    L += section(f"최근 5일간 {MOVE_PCT:g}% 이상 변동 (5일 누적 또는 하루 기준)", mv,
                 lambda r: [f"• {title(r)}  5일 {r['ret5']:+.1f}% (하루 최대 {r['maxday']:+.1f}%)"])

    # 2-6, 2-7: 50일 이격도 최대/최소 접근
    def note(r):
        return f" ※자료 {r['disp']['months']:.0f}개월뿐" if r["disp"]["months"] < 12 else ""

    hi = sorted([r for r in results if r["disp"] and r["disp"]["up"] >= NEAR_RATIO], key=lambda r: -r["disp"]["up"])
    lo = sorted([r for r in results if r["disp"] and r["disp"]["down"] >= NEAR_RATIO], key=lambda r: -r["disp"]["down"])
    L += section(f"50일 이격도가 과거 최대에 {NEAR_RATIO:.0%} 이상 접근", hi,
                 lambda r: [f"• {title(r)}  현재 {r['disp']['cur']:.1f} / 최대 {r['disp']['max']:.1f}"
                            f"({r['disp']['max_date']:%y.%m.%d}) → 접근도 {r['disp']['up']:.0%}{note(r)}"])
    L += section(f"50일 이격도가 과거 최소에 {NEAR_RATIO:.0%} 이상 접근", lo,
                 lambda r: [f"• {title(r)}  현재 {r['disp']['cur']:.1f} / 최소 {r['disp']['min']:.1f}"
                            f"({r['disp']['min_date']:%y.%m.%d}) → 접근도 {r['disp']['down']:.0%}{note(r)}"])
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
         f"RSI(14): {r['rsi']:.1f}",
         cross_detail("일봉", r["day"], "일"), cross_detail("주봉", r["week"], "주"), cross_detail("월봉", r["month"], "월")]
    d = r["disp"]
    if d:
        L.append(f"50일 이격도: 현재 {d['cur']:.1f} (과거 분포 하위 {d['pct']:.0f}%)")
        L.append(f"  최대 {d['max']:.1f} ({d['max_date']:%Y-%m-%d}) / 최소 {d['min']:.1f} ({d['min_date']:%Y-%m-%d})")
        L.append(f"  최대 접근도 {d['up']:.0%} / 최소 접근도 {d['down']:.0%}  (자료 {d['since']:%Y-%m-%d}~)")
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
    print(market_text(), "\n")
    print(summary_text(res), "\n")
    print(detail_section(res))
