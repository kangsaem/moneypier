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
NEAR_MAX = 0.9      # 50일 이격도가 (과거 최대 × 0.9) 이상이면 포함
NEAR_MIN = 1.1      # 50일 이격도가 (과거 최소 × 1.1) 이하이면 포함


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
    r["sig"] = stock_signals(r)
    return r


# ---------------------------------------------------------------- 종목 시그널
def stock_signals(r):
    """조건(싸다/과열) 2개 이상 + 트리거(크로스·RSI 방향전환) 1개 이상 → '타점', 조건만 충족 → '관심'"""
    d, dy = r["disp"], r["day"]
    recent = dy["ok"] and dy["cross"] and dy["cross"]["ago"] < CROSS_DAYS
    buy_c, buy_t, sell_c, sell_t = [], [], [], []
    bk, sk = {}, {}   # 강조용: 지표키 -> "c"(조건) / "t"(트리거)

    if r["rsi"] <= 35:
        buy_c.append(f"RSI {r['rsi']:.0f} (35 이하)"); bk["rsi"] = "c"
    if d and (d["cur"] <= 95 or d["down"] <= NEAR_MIN):
        buy_c.append(f"50일 이격도 {d['cur']:.1f} (낮은 구간)"); bk["disp"] = "c"
    if r["from_high"] <= -20:
        buy_c.append(f"52주 고점 대비 {r['from_high']:.0f}%"); bk["high"] = "c"
    if recent and dy["cross"]["golden"]:
        buy_t.append("5일 내 골든크로스"); bk["day"] = "t"
    if r["rsi_min5"] <= 35 and r["rsi"] > r["rsi_min5"] and r["rsi"] >= r["rsi_prev"]:
        buy_t.append("RSI 저점 찍고 반등"); bk["rsi"] = "t"

    if r["rsi"] >= 70:
        sell_c.append(f"RSI {r['rsi']:.0f} (70 이상)"); sk["rsi"] = "c"
    if d and (d["cur"] >= 110 or d["up"] >= NEAR_MAX):
        sell_c.append(f"50일 이격도 {d['cur']:.1f} (높은 구간)"); sk["disp"] = "c"
    if r["ret5"] >= MOVE_PCT:
        sell_c.append(f"5일 {r['ret5']:+.1f}% 급등"); sk["ret5"] = "c"
    if recent and not dy["cross"]["golden"]:
        sell_t.append("5일 내 데드크로스"); sk["day"] = "t"
    if r["rsi_max5"] >= 65 and r["rsi"] < r["rsi_max5"] and r["rsi"] <= r["rsi_prev"]:
        sell_t.append("RSI 고점 찍고 꺾임"); sk["rsi"] = "t"

    def level(c, t):
        return "타점" if (len(c) >= 2 and t) else ("관심" if len(c) >= 2 else None)

    # 추세 판단: 일봉·주봉이 모두 정배열(월봉은 계산 가능할 때만 확인)이면 '강한 상승추세'
    wk, mo = r["week"], r["month"]
    strong_up = bool(dy["ok"] and dy["above"] and wk["ok"] and wk["above"] and (not mo["ok"] or mo["above"]))
    sell = level(sell_c, sell_t)
    if sell == "관심" and strong_up:
        sell = "과열"          # 과열이지만 추세는 살아 있음 → 매도 신호가 아니라 '이익 보호' 구간
    return {"buy": level(buy_c, buy_t), "buy_c": buy_c, "buy_t": buy_t,
            "sell": sell, "sell_c": sell_c, "sell_t": sell_t, "strong_up": strong_up,
            "buy_k": bk, "sell_k": sk}


# ---------------------------------------------------------------- 분류
def flags(r):
    f = []
    d = r["day"]
    if d["ok"] and d["cross"] and d["cross"]["ago"] < CROSS_DAYS:
        f.append("최근골든" if d["cross"]["golden"] else "최근데드")
    if abs(r["ret5"]) >= MOVE_PCT or abs(r["maxday"]) >= MOVE_PCT:
        f.append(f"{MOVE_PCT:g}%↑변동")
    if r["disp"] and r["disp"]["up"] >= NEAR_MAX:
        f.append("이격도상단")
    if r["disp"] and r["disp"]["down"] <= NEAR_MIN:
        f.append("이격도하단")
    if r["sig"]["buy"]:
        f.insert(0, "매수" + r["sig"]["buy"])
    if r["sig"]["sell"]:
        f.insert(0, "매도" + r["sig"]["sell"])
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


def tl(r):
    return f"{r['name']} ({r['code']})  RSI {r['rsi']:.0f}"


def title(r):
    return f"{r['name']} ({r['code']})"


# ---------------------------------------------------------------- 1. 시장 지표
def _yf_close(sym):
    import yfinance as yf
    return yf.Ticker(sym).history(period="2mo")["Close"].dropna()


def market_snapshot():
    snap = {"ks": None, "vix": None, "y10": None, "y30": None, "err": []}
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
                      "days_since_low": int(days_since_low)}
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
    return snap


def _level(score, total, kind):
    ratio = score / total if total else 0
    hi, mid = ratio >= 0.6, ratio >= 0.3      # 항목 5개 기준: 3개 이상 / 2개 이상
    if kind == "risk":
        return ("경고", "lv2") if hi else ("주의", "lv1") if mid else ("낮음", "lv0")
    return ("바닥권 신호 다수", "lv2") if hi else ("바닥 형성 가능성", "lv1") if mid else ("신호 약함", "lv0")


def market_signals(snap, results):
    """시장 위험 / 시장 바닥 체크리스트. 항목 = (설명, 현재값, True|False|None(데이터없음))"""
    ks, vix, y10 = snap.get("ks"), snap.get("vix"), snap.get("y10")
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
    for key, nm, unit in (("vix", "미국 VIX", ""), ("y10", "미국채 10년물", "%"), ("y30", "미국채 30년물", "%")):
        v = snap.get(key)
        if v:
            L.append(f"{nm} {v['last']:.2f}{unit} (전일 대비 {v['chg']:+.2f}, {v['date']:%m-%d})")
    for e in snap.get("err", []):
        L.append(f"가져오기 실패: {e}")
    return "\n".join(L)


def _n(x):
    t = f"{x:.1f}"
    return t[:-2] if t.endswith(".0") else t


def extra_line(r, lv=None):
    """한 줄 요약: 50일 이격 현재 (최소/최대) 일/주/월 배열 · 10일선 대비"""
    d = r["disp"]
    parts = []
    if d:
        parts.append(f"50일 이격 {d['cur']:.1f} ({_n(d['min'])}/{_n(d['max'])})")
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
    groups = (("매수 타점", "buy", "타점"), ("매수 관심", "buy", "관심"), ("매도 타점", "sell", "타점"),
              ("과열 · 추세 유지 (매도 아님, 이익 보호 구간)", "sell", "과열"), ("매도 관심 (추세 약화)", "sell", "관심"))
    for head, side, lv in groups:
        hit = [r for r in results if r["sig"][side] == lv]
        L.append(f"\n▶ {head} [{len(hit)}]")
        if not hit:
            L.append("  해당 종목 없음")
        for r in hit:
            sg = r["sig"]
            L.append(f"• {tl(r)}")
            L.append("    조건: " + ", ".join(sg[side + "_c"]) + (" | 트리거: " + ", ".join(sg[side + "_t"]) if sg[side + "_t"] else ""))
            L.append("    " + extra_line(r, lv))
    return "\n".join(L)


# ---------------------------------------------------------------- 2. 스크리닝
def section(head, items, lines_fn):
    lines = []
    for r in items:
        lines.extend(lines_fn(r))
    return (head, len(items), lines)


def summary_sections(results):
    """스크리닝 결과를 [(제목, 종목수, 줄목록), ...] 로 반환"""
    S = []

    def ago_key(r):
        cr = r["day"]["cross"]
        return cr["ago"] if cr else 10 ** 6

    # 현재 일봉 배열 상태별로 '언제 바뀌었는지'
    bear = sorted([r for r in results if r["day"]["ok"] and not r["day"]["above"]], key=ago_key)
    bull = sorted([r for r in results if r["day"]["ok"] and r["day"]["above"]], key=ago_key)
    S.append(section("정배열 → 역배열로 바뀐 종목 (현재 일봉 역배열, 최근 전환순)", bear,
                     lambda r: [f"• {tl(r)}", trans_line(r)]))
    S.append(section("역배열 → 정배열로 바뀐 종목 (현재 일봉 정배열, 최근 전환순)", bull,
                     lambda r: [f"• {tl(r)}", trans_line(r)]))

    # 최근 N일 크로스
    def recent(golden):
        rs = [r for r in results if r["day"]["ok"] and r["day"]["cross"]
              and r["day"]["cross"]["golden"] == golden and r["day"]["cross"]["ago"] < CROSS_DAYS]
        return sorted(rs, key=ago_key)

    def cross_lines(r):
        cr = r["day"]["cross"]
        when = "최근 거래일" if cr["ago"] == 0 else f"{cr['ago']}거래일 전"
        return [f"• {tl(r)}", f"    {cr['date']:%m-%d} ({when}), 현재 5/10 이격 {r['day']['gap']:+.2f}%"]

    S.append(section(f"최근 {CROSS_DAYS}일간 골든크로스 (5일선이 10일선 상향돌파)", recent(True), cross_lines))
    S.append(section(f"최근 {CROSS_DAYS}일간 데드크로스 (5일선이 10일선 하향돌파)", recent(False), cross_lines))

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
                                f" (과거 최대 {r['disp']['max']:.1f}, {r['disp']['max_date']:%y.%m.%d}){note(r)}"]))
    S.append(section(f"50일 이격도가 과거 최소 × {NEAR_MIN:g} 이하", lo,
                     lambda r: [f"• {tl(r)}",
                                f"    현재 {r['disp']['cur']:.1f} ≤ 기준 {r['disp']['min_thr']:.1f}"
                                f" (과거 최소 {r['disp']['min']:.1f}, {r['disp']['min_date']:%y.%m.%d}){note(r)}"]))
    return S


def summary_text(results):
    L = ["■ 2. 종목 스크리닝", "표기: 일/주/월 = 5선·10선 기준, ▲역→정배열(골든) ▼정→역배열(데드), 괄호는 전환 후 경과"]
    for head, n, lines in summary_sections(results):
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
