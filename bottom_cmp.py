"""시장 바닥 지표 시험(2026-10-15): 현행(4개 중 2개) vs 사용자 제안(과매도 → 하락 둔화 → 반등 확인, 단계형)
코스피 1996~. 외국인 순매수 = 네이버 m.stock api/index/KOSPI/trend?bizdate (하루 1번 호출, 억원) → results/foreign_kospi.json 에 모아 둠(다음 실행은 새 날짜만).
'처음 켜진 날' = 꺼진 지 20거래일 이상 뒤 다시 켜진 날. 그날 종가 매수 시 20·60·120거래일 뒤 수익률, 그 120일 안 최저 종가까지 추가 하락, 120일 안 최저점까지 걸린 날.
결과: results/bottom_cmp.txt"""
import contextlib, io, json, os, time
from concurrent.futures import ThreadPoolExecutor
import pandas as pd, requests
import market_report as m

START, GAP, HZ, WIN = "1995-01-01", 20, (20, 60, 120), int(os.environ.get("BC_WIN", "20"))
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/126 Safari/537.36", "Referer": "https://m.stock.naver.com/"}
FF = "results/foreign_kospi.json"
out = []
P = lambda *a: (print(*a), out.append(" ".join(str(x) for x in a)))


def close(fc, yc, start=START):
    try:
        with contextlib.redirect_stdout(io.StringIO()):
            s = m.fdr.DataReader(fc, start)["Close"].dropna()
        if len(s) > 1000:
            return s
    except Exception:
        pass
    import yfinance as yf
    s = yf.Ticker(yc).history(start=start)["Close"].dropna()
    s.index = s.index.tz_localize(None)
    return s


def foreign(days):
    try:
        have = json.load(open(FF))
    except Exception:
        have = {}
    need = [d.strftime("%Y%m%d") for d in days if d.year >= 1999 and d.strftime("%Y%m%d") not in have]

    def one(d):
        for k in range(3):
            try:
                j = requests.get(f"https://m.stock.naver.com/api/index/KOSPI/trend?bizdate={d}", headers=H, timeout=15).json()
                if j.get("bizdate") == d:
                    return d, float(str(j["foreignValue"]).replace(",", "").replace("+", ""))
                return d, None
            except Exception:
                time.sleep(1 + k)
        return d, None
    t0 = time.time()
    with ThreadPoolExecutor(8) as ex:
        for d, v in ex.map(one, need):
            if v is not None:
                have[d] = v
    print(f"외국인 순매수 새로 받음 {len(need)}일 ({time.time() - t0:.0f}초)")
    os.makedirs("results", exist_ok=True)
    json.dump(have, open(FF, "w"), separators=(",", ":"))
    s = pd.Series({pd.Timestamp(k): v for k, v in have.items()}).sort_index()
    nz = s[s != 0]
    first = nz.index[0] if len(nz) else None          # 0만 나오는 초기 구간 = 자료 없음
    return s[s.index >= first] if first is not None else s.iloc[0:0]


ks = close("KS11", "^KS11")
vix = close("VIX", "^VIX").reindex(ks.index).ffill()
fr = foreign(ks.index).reindex(ks.index)
F0 = fr.first_valid_index()
P(f"코스피 {ks.index[0]:%Y-%m-%d}~{ks.index[-1]:%Y-%m-%d} · 외국인 순매수 자료 {F0:%Y-%m-%d}~ ({fr.notna().sum()}일) · 단계 기억 창 {WIN}거래일")

r = m.rsi(ks)
disp = ks / ks.rolling(50).mean() * 100
hi = ks.rolling(250, min_periods=60).max()
dd = ks / hi - 1
lo_age = ks.rolling(250, min_periods=60).apply(lambda w: len(w) - 1 - w.argmin(), raw=True)

# 현행 4개(내 종목 RSI 제외)
old = pd.DataFrame({"disp": disp <= 90, "rsi": r <= 35, "dd_nolow": (dd <= -0.20) & (lo_age >= 20), "vix": vix >= 30})
old_n = old.sum(axis=1)

# 제안 ① 과매도
g1 = pd.DataFrame({"disp": disp <= 90, "dd": dd <= -0.20, "rsi": r <= 35})
# 제안 ② 하락 둔화
#  상승 다이버전스: 최근 5일 최저 종가 < 6~20일 전 최저 종가인데 그날 RSI는 더 높음('종가 하락 + RSI 상승'을 하루로 보면 RSI 계산상 늘 거짓이라 저점 비교로 정의)
lo_a = ks.rolling(5).min(); lo_b = ks.shift(5).rolling(15).min()
ia = ks.rolling(5).apply(lambda w: w.argmin(), raw=True); ib = ks.shift(5).rolling(15).apply(lambda w: w.argmin(), raw=True)
ra = pd.Series([r.iloc[i - 4 + int(a)] if pd.notna(a) and i >= 4 else float("nan") for i, a in enumerate(ia)], index=ks.index)
rb = pd.Series([r.iloc[i - 19 + int(b)] if pd.notna(b) and i >= 19 else float("nan") for i, b in enumerate(ib)], index=ks.index)
div = (lo_a < lo_b) & (ra > rb)
no_low20 = ~((ks <= ks.shift(1).rolling(20).min()).astype(float).rolling(20).max().fillna(1).astype(bool))   # 최근 20거래일 동안 '직전 20일 최저 종가 이하'로 마감한 날 없음
f20 = fr.rolling(20, min_periods=15).sum(); f20p = f20.shift(20)
f_less = (f20p < 0) & (f20 > f20p)                     # 20일 누적 순매도 규모가 직전 20일보다 축소
g2 = pd.DataFrame({"div": div, "nolow": no_low20, "f_less": f_less})
# 제안 ③ 반등 확인
f_turn = (f20 > 0) & (f20p <= 0)                        # 20일 누적이 플러스로 전환(직전 20일 누적은 0 이하)
brk5 = ks > ks.shift(1).rolling(5).max()
brk10 = ks > ks.shift(1).rolling(10).max()
g3 = pd.DataFrame({"f_turn": f_turn, "brk5": brk5})


def recent(s, n=WIN):
    return s.rolling(n, min_periods=1).max().astype(bool)


def stages(use_foreign=True):
    a1 = g1.sum(axis=1) >= 2
    c2 = g2[["div", "nolow"] + (["f_less"] if use_foreign else [])].sum(axis=1)
    a2 = recent(a1) & (c2 >= 2)
    c3 = g3[(["f_turn"] if use_foreign else []) + ["brk5"]].sum(axis=1)
    a3 = recent(a2) & (c3 >= 1)
    a3s = a3 & brk10
    return a1, a2, a3, a3s


def events(flag):
    flag = flag.fillna(False).astype(bool)
    ev, off = [], GAP
    for d, v in flag.items():
        if v and off >= GAP:
            ev.append(d)
        off = 0 if v else off + 1
    return ev


def stat(name, evs, base_from=None):
    rows = []
    idx = ks.index
    for d in evs:
        i = idx.get_loc(d)
        if i + 120 >= len(idx):
            fut = None
        rets = [ (ks.iloc[i + h] / ks.iloc[i] - 1) * 100 if i + h < len(idx) else None for h in HZ]
        w = ks.iloc[i:i + 121]
        mdd = (w.min() / ks.iloc[i] - 1) * 100
        tmin = int(w.values.argmin())
        rows.append((d, rets, mdd, tmin))
    done = [x for x in rows if x[1][2] is not None]
    if not rows:
        P(f"{name:<34} 0회"); return
    avg = lambda k: sum(x[1][k] for x in rows if x[1][k] is not None) / max(1, sum(1 for x in rows if x[1][k] is not None))
    win60 = sum(1 for x in rows if x[1][1] is not None and x[1][1] > 0) / max(1, sum(1 for x in rows if x[1][1] is not None)) * 100
    md = sorted(x[2] for x in rows); mid_mdd = md[len(md) // 2]
    P(f"{name:<34} {len(rows):>3}회 | 20일 {avg(0):+6.1f}% · 60일 {avg(1):+6.1f}% · 120일 {avg(2):+6.1f}% | 60일 플러스 {win60:4.0f}%"
      f" | 추가 하락 중간 {mid_mdd:+6.1f}% · 최악 {md[0]:+6.1f}% | 120일 안 최저점까지 중간 {sorted(x[3] for x in rows)[len(rows)//2]}일")
    return rows


def base(sel):
    idx = ks.index
    v = {h: [] for h in HZ}
    for i, d in enumerate(idx):
        if not sel(d):
            continue
        for h in HZ:
            if i + h < len(idx):
                v[h].append((ks.iloc[i + h] / ks.iloc[i] - 1) * 100)
    return " · ".join(f"{h}일 {sum(v[h]) / len(v[h]):+.1f}%" for h in HZ)


for scope, since, fo in (("A. 1996~ (외국인 항목 없이)", pd.Timestamp("1996-06-01"), False), (f"B. {F0:%Y}~ (외국인 포함)", F0 + pd.Timedelta(days=60), True),
                         (f"C. {F0:%Y}~ (외국인 없이 — B와 같은 기간)", F0 + pd.Timedelta(days=60), False)):
    P(f"\n===== {scope} · 아무 날 평균: {base(lambda d: d >= since)}")
    a1, a2, a3, a3s = stages(fo)
    cut = lambda s: s[s.index >= since]
    stat("현행: 4개 중 2개 이상", events(cut(old_n >= 2)))
    stat("현행: 4개 중 3개 이상", events(cut(old_n >= 3)))
    stat("제안 ① 과매도(3개 중 2개)", events(cut(a1)))
    stat("제안 ② 하락 둔화(① 뒤 + 2개)", events(cut(a2)))
    rows3 = stat("제안 ③ 반등 확인(② 뒤 + 1개)", events(cut(a3)))
    stat("제안 ③ + 강화(10일 최고 돌파)", events(cut(a3s)))
    if rows3:
        P("  ③ 켜진 날: " + ", ".join(f"{d:%Y-%m-%d}({x[1]:+.0f}%)" if x[1] is not None else f"{d:%Y-%m-%d}" for d, x, _, _ in [(d, rr, a, b) for d, rr, a, b in rows3]))

# 항목별 켜진 비율(상승장에서도 늘 켜지는 항목 확인)
P("\n항목별 켜진 날 비율(1996~): " + " · ".join(f"{k} {v.mean() * 100:.0f}%" for k, v in
   {"이격도≤90": g1.disp, "고점-20%": g1.dd, "RSI≤35": g1.rsi, "다이버전스": div, "20일 신저점 없음": no_low20,
    "외국인 순매도 축소": f_less[f_less.index >= F0], "외국인 순매수 전환": f_turn[f_turn.index >= F0], "5일 최고 돌파": brk5, "VIX≥30": old.vix}.items()))
os.makedirs("results", exist_ok=True)
open("results/bottom_cmp.txt", "w", encoding="utf-8").write("\n".join(out))
