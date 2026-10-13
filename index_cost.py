"""지수 칩 규칙 — 세금·이자·배당 반영 + 나눠서 다시 사기 + 투입별 손익 기록 (2026-10-13, 리포트와 무관한 별도 시험)

기준 규칙(머니파이 지수 칩, index_cmp.py IC_SET=chips 와 같은 판정·체결: 그날 종가 판정 → 다음 거래일 종가 체결, 비용 매수 0.05% · 매도 0.25%)
  코스피 cut30d10 · S&P500 cut50d10 · 나스닥100 cut50c10 · 코스닥 cut50c5 (현금 n%) / +C2R3 = 추가 매수(깊은 하락 뒤 200일선 회복 +100%, 다시 깨지면 회수)
  현금 투입(되사기) = 200일선 위로 올라선 첫날 종가 신호 → 다음 날 종가 체결(확인 기간 없음)

시험 1 — 나눠서 다시 사기: 현금화한 뒤 바닥 신호가 새로 켜지면(전날 꺼짐 → 오늘 켜짐, 아직 200일선 아래) 그때 현금의 절반을 사고,
  나머지는 200일선 회복 때. 바닥 신호가 안 켜지면 회복 때 전부.
  sA = 바닥 단계 1/3 이상(market_report._turn_stage, 지수 종가로) / sB = 시장 바닥 지표 4개 중 2개 이상
  (50일 이격도 90 이하 · RSI 35 이하 · 52주 고점 −20% 이하 + 20거래일 신저점 없음 · VIX 30 이상 — 코스피는 리포트와 같고 미국 지수는 그 지수로 계산)
시험 2 — 투입별 기록(전체 기간, 세금 없음): 현금화 → 되사기 한 번마다 판 가격 대비 되산 가격(이득 = 싸게 되삼), 추가 매수 한 번마다 손익,
  성공·실패 수, 실패 평균·최악, 최대 연속 실패와 그 기간
시험 3 — 시나리오
  N  세금·이자·배당 없음(기존 시험과 같음 — 일치 확인용)
  G  배당 재투자 + 현금 이자, 세금 없음(ISA·연금 계좌 근사)
  T1 일반 계좌 국내 상장 ETF: 배당·이자 15.4%, 코스피·코스닥 ETF 매매차익 비과세, 해외 지수 ETF 매매차익 15.4%(팔 때마다, 손실 상계 없음)
  T2 해외 직구 ETF(미국 지수만): 배당 15%(원천), 이자 15.4%, 양도차익 연간 합산(손익 상계) − 250만 원 공제 × 22% (시드 1억 원 가정 → 공제 = 시드의 2.5%)
  모든 세금 시나리오는 마지막 날 전부 판다고 보고 세후 금액으로 비교(보유도 같음).
  추가 매수 자금은 밖의 돈 → 넣어 둔 동안 받을 수 있던 이자(세후)를 비용으로 뺌.
자료: 배당 = S&P500·나스닥100은 야후 총수익지수(^SP500TR·^XNDX)로 연도별 배당률 계산, 없는 해는 고정값(S&P 2.0% · 나스닥100 0.8%),
      코스피 1.7% · 코스닥 0.7% 고정. 이자 = FRED 미국 3개월 국채(DTB3), 한국 3개월 금리(IR3TIB01KRM156N, 월), 실패하면 미국 3% · 한국 3.5% 고정.
한계: 환율·ETF 보수·금융소득종합과세·건강보험료 미반영, 미국 지수는 달러 기준. 배당은 고르게 들어온다고 보고 분기마다 지급.
결과: results/index_cost.json · index_cost.html
"""
import json
import os
import re
from statistics import mean, median

import numpy as np
import pandas as pd

import index_cmp as ic
import market_report as m

BUY, SELL = ic.BUY, ic.SELL
WIN = 1260
CUT = {"kospi": "cut30d10", "spx": "cut50d10", "ndx": "cut50c10", "kosdaq": "cut50c5"}
US = ("spx", "ndx")
DIV_FIX = {"spx": 0.020, "ndx": 0.008, "kospi": 0.017, "kosdaq": 0.007}
TR = {"spx": "^SP500TR", "ndx": "^XNDX"}
RATE_FIX = {"us": 0.03, "kr": 0.035}
SCEN = {
    "N": dict(div=False, int=False, td=0.0, ti=0.0, cg=None),
    "G": dict(div=True, int=True, td=0.0, ti=0.0, cg=None),
    "T1": dict(div=True, int=True, td=0.154, ti=0.154, cg="sale"),     # 코스피·코스닥은 cg 없음(아래에서 바꿈)
    "T2": dict(div=True, int=True, td=0.15, ti=0.154, cg="year"),
}
CG_SALE, CG_YEAR, CG_DED = 0.154, 0.22, 0.025


def rules_for(key):
    c = CUT[key]
    r = ["hold", c, f"{c}sA", f"{c}sB"]
    if key != "kosdaq":
        r += ["C2R3", f"{c}+C2R3", f"{c}sA+C2R3", f"{c}sB+C2R3"]
    return r


# ---------------------------------------------------------------- 자료
def fred(sid):
    import io
    import requests
    t = requests.get(f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={sid}", timeout=30).text
    df = pd.read_csv(io.StringIO(t))
    df.columns = ["d", "v"]
    df["d"] = pd.to_datetime(df["d"])
    df["v"] = pd.to_numeric(df["v"], errors="coerce")
    s = df.dropna().set_index("d")["v"] / 100
    if len(s) < 100:
        raise ValueError("짧음")
    return s


def yahoo(code):
    import yfinance as yf
    s = yf.Ticker(code).history(start="1987-01-01")["Close"].dropna()
    s.index = s.index.tz_localize(None)
    return s


def div_yield_by_year(key, s, note):
    """연도별 배당률(가격지수 대비 총수익지수의 초과 상승)"""
    fixed = DIV_FIX[key]
    out = {}
    tr = None
    if key in TR:
        try:
            tr = yahoo(TR[key])
        except Exception as e:
            note.append(f"{key} 총수익지수 실패 {e}")
    py = s.groupby(s.index.year).last()
    if tr is not None and len(tr) > 300:
        ty = tr.groupby(tr.index.year).last()
        first = tr.index[0].year
        for y in py.index:
            if y - 1 in ty.index and y in ty.index and y - 1 > first - 1 and y - 1 in py.index and tr.index[0] <= pd.Timestamp(f"{y - 1}-12-31"):
                if y - 1 == first and tr.index[0] > pd.Timestamp(f"{y - 1}-01-10"):
                    continue
                v = (ty[y] / ty[y - 1]) / (py[y] / py[y - 1]) - 1
                if 0 <= v <= 0.06:
                    out[int(y)] = float(v)
        note.append(f"{key} 배당: 총수익지수로 {len(out)}개 연도(평균 {mean(out.values()) * 100:.2f}%), 나머지 {fixed * 100:.1f}% 고정" if out else f"{key} 배당 {fixed * 100:.1f}% 고정")
    else:
        note.append(f"{key} 배당 {fixed * 100:.1f}% 고정")
    return {int(y): out.get(int(y), fixed) for y in range(s.index[0].year, s.index[-1].year + 1)}


class Ix:
    pass


def prep(key, nm, s, vix, rates, note, need_bottom=True):
    ix = Ix()
    ix.key, ix.name, ix.s = key, nm, s
    P = s.to_numpy(dtype=float)
    ix.P, ix.dates = P, s.index
    ix.years = s.index.year.to_numpy()
    ix.month = s.index.month.to_numpy()
    MA = pd.Series(P).rolling(200).mean().to_numpy()
    PK = pd.Series(P).rolling(252, min_periods=1).max().to_numpy()
    below = np.nan_to_num(P < MA, nan=False).astype(bool)
    below[:200] = False
    peak_dd = np.zeros(len(P))
    s0 = None
    for i in range(201, len(P)):
        if below[i] and not below[i - 1]:
            s0 = i
        if not below[i] and below[i - 1] and s0 is not None:
            pk = P[max(0, s0 - 252):s0 + 1].max()
            peak_dd[i] = P[s0:i].min() / pk - 1
    ix.MA, ix.PK, ix.below, ix.peak_dd = MA, PK, below, peak_dd
    # 배당(일 단위) · 이자(일 단위)
    dy = div_yield_by_year(key, s, note)
    cnt = pd.Series(1, index=s.index).groupby(s.index.year).transform("sum").to_numpy()
    ix.dy = np.array([dy[int(y)] for y in ix.years]) / cnt
    ix.divs = dy
    r = rates.get("us" if key in US else "kr")
    if r is not None:
        ix.rate = r.reindex(s.index.union(r.index)).ffill().reindex(s.index).bfill().fillna(RATE_FIX["us" if key in US else "kr"]).to_numpy() / 252
    else:
        ix.rate = np.full(len(P), RATE_FIX["us" if key in US else "kr"] / 252)
    # 바닥 신호
    n = len(P)
    ix.botA = np.zeros(n, dtype=bool)
    ix.botB = np.zeros(n, dtype=bool)
    if need_bottom:
        rs = m.rsi(s)
        dd = s / s.rolling(250, min_periods=60).max() - 1
        ddmin = dd.rolling(121, min_periods=1).min().to_numpy()
        for i in range(260, n):
            if ddmin[i] <= -m.BOTTOM_DROP / 100 + 1e-9:
                try:
                    lv, _ = m._turn_stage(s.iloc[:i + 1], rs.iloc[:i + 1])
                except Exception:
                    lv = 0
                ix.botA[i] = lv >= 1
        disp = (s / s.rolling(50).mean() * 100).to_numpy()
        lo_age = s.rolling(250, min_periods=60).apply(lambda w: len(w) - 1 - w.argmin(), raw=True).to_numpy()
        v = vix.reindex(s.index.union(vix.index)).ffill().reindex(s.index).to_numpy() if vix is not None else np.full(n, np.nan)
        rv = rs.to_numpy()
        ddv = dd.to_numpy()
        cnt4 = ((disp <= 90).astype(int) + (rv <= 35).astype(int) + ((ddv <= -0.20) & (lo_age >= 20)).astype(int) + (v >= 30).astype(int))
        ix.botB = cnt4 >= 2
    return ix


# ---------------------------------------------------------------- 모의 운용
def sim2(ix, i0, i1, rule, sc, log=False):
    P, below, peak_dd, PK = ix.P, ix.below, ix.peak_dd, ix.PK
    parts = rule.split("+")
    mc = re.fullmatch(r"cut(\d+)(?:c(\d+))?(?:d(\d+))?(?:s([AB]))?", parts[0])
    inj = "C2R3" in parts
    if mc:
        cut, conf, depth, split = int(mc.group(1)) / 100, int(mc.group(2) or 1), int(mc.group(3) or 0) / 100, mc.group(4)
    else:
        cut, conf, depth, split = 0.0, 1, 0.0, None
    bot = ix.botA if split == "A" else ix.botB if split == "B" else None
    cg = sc["cg"]
    td, ti = sc["td"], sc["ti"]
    st = {"base": 0.0, "x": 0.0, "C": 0.0, "yg": 0.0, "tax": 0.0}
    cash, L = 0.0, 0.0

    def sell(u, p):
        U = st["base"] + st["x"]
        pr = u * p * (1 - SELL)
        basis = st["C"] * u / U if U > 0 else 0.0
        st["C"] -= basis
        g = pr - basis
        t = 0.0
        if cg == "sale" and g > 0:
            t = g * CG_SALE
        elif cg == "year":
            st["yg"] += g
        st["tax"] += t
        return pr, pr - t

    def buy(a, p):
        st["C"] += a
        return a * (1 - BUY) / p

    st["base"] = buy(1.0, P[i0])
    reduced, fired, brun, rb_pend, half_done, cut_i = False, False, 0, False, False, None
    pend, own = [], []
    div_acc = 0.0
    cur_y = ix.years[i0]
    ev_cut, ev_inj = [], []
    ev, xi = None, None
    for i in range(i0, i1 + 1):
        p = P[i]
        if cg == "year" and ix.years[i] != cur_y:
            t = max(0.0, st["yg"] - CG_DED) * CG_YEAR
            cash -= t
            st["tax"] += t
            st["yg"], cur_y = 0.0, ix.years[i]
        if i > i0:
            r = ix.rate[i] if sc["int"] else 0.0
            if r:
                if cash > 0:
                    it = cash * r
                    cash += it * (1 - ti)
                    st["tax"] += it * ti
                if L > 0:
                    c_ = L * r * (1 - ti)
                    cash -= c_
                    if xi:
                        xi["cost"] += c_
            if sc["div"]:
                div_acc += (st["base"] + st["x"]) * p * ix.dy[i]
                if (ix.month[i] != ix.month[i - 1] and ix.month[i] in (1, 4, 7, 10)) or i == i1:
                    cash += div_acc * (1 - td)
                    st["tax"] += div_acc * td
                    div_acc = 0.0
                    if not reduced and cash > 0 and i < i1:
                        st["base"] += buy(cash, p)
                        cash = 0.0
        for act in pend:
            if act == "cut" and not reduced:
                E = st["base"] * p + cash
                need = cut * E - cash
                if need > 0:
                    f = min(1.0, need / (st["base"] * p * (1 - SELL)))
                    u = st["base"] * f
                    _, net = sell(u, p)
                    st["base"] -= u
                    cash += net
                reduced, half_done, cut_i = True, False, i
                ev = {"cut": str(ix.dates[i].date()), "cpx": p, "buys": []}
            elif act == "half" and reduced and not half_done:
                a = cash * 0.5 if cash > 0 else 0.0
                st["base"] += buy(a, p)
                cash -= a
                half_done = True
                if ev is not None:
                    ev["buys"].append((str(ix.dates[i].date()), p, 0.5))
            elif act == "rebuy" and reduced:
                a = cash if cash > 0 else 0.0
                st["base"] += buy(a, p)
                cash -= a
                reduced, rb_pend = False, False
                if ev is not None:
                    ev["buys"].append((str(ix.dates[i].date()), p, 0.5 if half_done else 1.0))
                    w = sum(b[2] / b[1] for b in ev["buys"])
                    ev["gain"] = (1 - SELL) * (1 - BUY) * ev["cpx"] * w - 1
                    ev["days"] = i - cut_i
                    ev_cut.append(ev)
                    ev = None
            elif act == "inject" and st["x"] <= 0:
                E = st["base"] * p + cash
                if E > 0:
                    st["x"] = buy(E, p)
                    L = E
                    xi = {"in": str(ix.dates[i].date()), "px": p, "L": E, "cost": 0.0, "i": i}
            elif act == "out" and st["x"] > 0:
                pr, net = sell(st["x"], p)
                st["x"] = 0.0
                cash += net - L
                if xi:
                    xi.update({"out": str(ix.dates[i].date()), "pnl": (pr - L) / L, "pnl_c": (net - L - xi["cost"]) / L, "days": i - xi["i"]})
                    ev_inj.append(xi)
                xi, L = None, 0.0
        pend = []
        b, pb = below[i], below[i - 1]
        if mc:
            brun = brun + 1 if b else 0
            if not b:
                fired = False
            if b and not fired and not reduced and brun >= conf and (depth == 0 or p / PK[i] - 1 <= -depth):
                pend.append("cut")
                fired = True
            if reduced and not b and not rb_pend and pb:
                pend.append("rebuy")
                rb_pend = True
            if bot is not None and reduced and not half_done and not rb_pend and b and cut_i is not None and i > cut_i and bot[i] and not bot[i - 1]:
                pend.append("half")
        if b and not pb:
            if inj and st["x"] > 0:
                pend.append("out")
        if not b and pb:
            if inj and peak_dd[i] <= -0.15:
                pend.append("inject")
        own.append((st["base"] + st["x"]) * p + cash - L)
    v = np.array(own)
    final = v[-1]
    if cg:                                                    # 마지막 날 전부 판다고 보고 세후
        U = st["base"] + st["x"]
        g = U * P[i1] * (1 - SELL) - st["C"]
        t = max(0.0, g) * CG_SALE if cg == "sale" else max(0.0, st["yg"] + g - CG_DED) * CG_YEAR
        final -= t
        st["tax"] += t
    if xi and st["x"] > 0:
        pr = st["x"] * P[i1] * (1 - SELL)
        xi.update({"out": str(ix.dates[i1].date()) + "(끝)", "pnl": (pr - L) / L, "pnl_c": (pr - L - xi["cost"]) / L, "days": i1 - xi["i"]})
        ev_inj.append(xi)
    mdd = float(((v / np.maximum.accumulate(v)) - 1).min() * 100)
    out = {"roc": float((final - 1) * 100), "mdd": mdd, "tax": st["tax"] * 100, "path": v}
    if log:
        out["ev_cut"], out["ev_inj"] = ev_cut, ev_inj
    return out


def ev_stats(evs, k):
    if not evs:
        return {"n": 0}
    g = [e[k] * 100 for e in evs]
    fails = [x for x in g if x < 0]
    run = best = 0
    s_i = best_s = best_e = None
    for j, x in enumerate(g):
        if x < 0:
            if run == 0:
                s_i = j
            run += 1
            if run > best:
                best, best_s, best_e = run, s_i, j
        else:
            run = 0
    d0 = evs[best_s].get("cut") or evs[best_s].get("in") if best else None
    d1 = (evs[best_e].get("buys") or [[None]])[-1][0] if best and "buys" in evs[best_e] else (evs[best_e].get("out") if best else None)
    return {"n": len(g), "win": sum(x >= 0 for x in g), "fail": len(fails), "mean": mean(g), "med": median(g),
            "fail_mean": mean(fails) if fails else None, "worst": min(g), "best": max(g),
            "streak": best, "streak_from": d0, "streak_to": d1, "streak_sum": sum(g[best_s:best_e + 1]) if best else 0}


def agg_windows(ix, rules, scens):
    n = len(ix.P)
    start = 260
    starts, last_m = [], None
    for i in range(start, n - WIN):
        mo = (ix.dates[i].year, ix.dates[i].month)
        if mo != last_m:
            starts.append(i)
            last_m = mo
    res = {}
    for sn in scens:
        sc = dict(SCEN[sn])
        if sn == "T1" and ix.key not in US:
            sc["cg"] = None
        W = {r: [sim2(ix, i0, i0 + WIN - 1, r, sc) for i0 in starts] for r in rules}
        F = {r: sim2(ix, start, n - 1, r, sc) for r in rules}
        hold = [w["roc"] for w in W["hold"]]
        yrs = (n - 1 - start) / 252
        a = {}
        for r in rules:
            rr = [w["roc"] for w in W[r]]
            srt = sorted(rr)
            fr = F[r]["roc"]
            a[r] = {"mean": mean(rr), "med": median(rr), "p10": srt[int(0.1 * (len(srt) - 1))], "worst": srt[0], "loss": sum(x < 0 for x in rr),
                    "mdd": mean(w["mdd"] for w in W[r]), "mdd_worst": min(w["mdd"] for w in W[r]),
                    "beat_hold": sum(x > h for x, h in zip(rr, hold)), "tax": mean(w["tax"] for w in W[r]),
                    "full": fr, "full_cagr": ((1 + fr / 100) ** (1 / yrs) - 1) * 100 if fr > -100 else None, "full_mdd": F[r]["mdd"]}
        res[sn] = a
        print(f"{ix.name} {sn}: " + " | ".join(f"{r} {a[r]['mean']:+.0f}/{a[r]['p10']:+.0f}/{a[r]['mdd']:.0f}" for r in rules))
    return res, len(starts), str(ix.dates[starts[0]].date()), str(ix.dates[starts[-1]].date())


def main():
    note = []
    rates = {}
    for k, sid in (("us", "DTB3"), ("kr", "IR3TIB01KRM156N")):
        try:
            rates[k] = fred(sid)
            note.append(f"이자 {k}: FRED {sid} {rates[k].index[0].date()}~{rates[k].index[-1].date()} 평균 {rates[k].mean() * 100:.2f}%")
        except Exception as e:
            rates[k] = None
            note.append(f"이자 {k}: FRED {sid} 실패({e}) → {RATE_FIX[k] * 100:.1f}% 고정")
    try:
        vix = m._index_close("VIX", "^VIX")
        vix = vix[~vix.index.duplicated()].sort_index()
    except Exception as e:
        vix = None
        note.append(f"VIX 실패 {e}")
    out = {"note": note, "idx": {}}
    for key, nm, fc, yc in ic.IDX:
        s = ic.load(fc, yc)
        ix = prep(key, nm, s, vix, rates, note)
        rules = rules_for(key)
        scens = ["N", "G", "T1"] + (["T2"] if key in US else [])
        agg, nw, w0, w1 = agg_windows(ix, rules, scens)
        ev = {}
        for r in rules[1:]:
            x = sim2(ix, 260, len(ix.P) - 1, r, SCEN["N"], log=True)
            xg = sim2(ix, 260, len(ix.P) - 1, r, SCEN["G"], log=True)
            ev[r] = {"cut": ev_stats(x["ev_cut"], "gain"), "inj": ev_stats(x["ev_inj"], "pnl"), "inj_cost": ev_stats(xg["ev_inj"], "pnl_c"),
                     "cut_list": [{**e, "buys": e["buys"], "gain": e["gain"] * 100} for e in x["ev_cut"]],
                     "inj_list": [{k: (v * 100 if k in ("pnl", "pnl_c") else v) for k, v in e.items() if k not in ("i",)} for e in x["ev_inj"]]}
        out["idx"][key] = {"name": nm, "from": str(s.index[0].date()), "to": str(s.index[-1].date()), "n_win": nw, "win_from": w0, "win_to": w1,
                           "rules": rules, "agg": agg, "ev": ev,
                           "botA_days": int(ix.botA.sum()), "botB_days": int(ix.botB.sum()),
                           "div_mean": mean(ix.divs.values()) * 100, "rate_mean": float(ix.rate[260:].mean() * 252 * 100)}
        print(f"{nm}: 바닥단계 켜진 날 {ix.botA.sum()} · 바닥지표2+ {ix.botB.sum()} · 배당 평균 {out['idx'][key]['div_mean']:.2f}% · 이자 평균 {out['idx'][key]['rate_mean']:.2f}%")
        for r in rules[1:]:
            e = ev[r]
            print(f"  {r} 되사기 {e['cut'].get('n')}회 이득 {e['cut'].get('win')} 평균 {e['cut'].get('mean', 0):+.1f}% · 추가매수 {e['inj'].get('n')}회 실패 {e['inj'].get('fail')} 연속실패 {e['inj'].get('streak')}")
    for n_ in note:
        print(n_)
    os.makedirs("results", exist_ok=True)
    json.dump(out, open("results/index_cost.json", "w", encoding="utf-8"), ensure_ascii=False, indent=0,
              default=lambda o: o.item() if hasattr(o, "item") else str(o))
    write_html(out)


def write_html(out):
    SN = {"N": "세금·이자·배당 없음(기존)", "G": "배당·이자 O · 세금 없음(ISA·연금)", "T1": "일반 계좌 국내 상장 ETF", "T2": "해외 직구 ETF"}
    h = ["<!doctype html><meta charset='utf-8'><meta name='viewport' content='width=device-width,initial-scale=1'><title>지수 칩 세후 시험</title>",
         "<style>body{font:13px/1.5 -apple-system,'Noto Sans KR',sans-serif;margin:12px;background:#1E1F22;color:#E8ECF3}table{border-collapse:collapse;margin:6px 0 14px}"
         "td,th{border-bottom:1px solid #3a3b40;padding:3px 6px;text-align:right;white-space:nowrap}th{color:#9A9BA1;font-weight:500}.l{text-align:left}div{overflow-x:auto}p{color:#9A9BA1}</style>",
         "<h1 style='font-size:16px'>지수 칩 규칙 — 세금·이자·배당 · 나눠 사기 · 투입 기록</h1>"]
    h += [f"<p>{x}</p>" for x in out["note"]]
    for key, d in out["idx"].items():
        h.append(f"<h2 style='font-size:14px'>{d['name']} {d['from']}~{d['to']} · 5년 구간 {d['n_win']}개 · 배당 평균 {d['div_mean']:.2f}% · 이자 평균 {d['rate_mean']:.2f}%</h2>")
        for sn, a in d["agg"].items():
            h.append(f"<div><table><tr><th class='l'>{SN[sn]}</th><th>평균</th><th>중앙</th><th>하위10%</th><th>최악</th><th>손실</th><th>MDD 평균</th><th>MDD 최악</th><th>보유보다↑</th><th>세금</th><th>전체</th><th>연</th><th>전체 MDD</th></tr>")
            for r, x in a.items():
                h.append(f"<tr><td class='l'>{r}</td><td>{x['mean']:+.0f}</td><td>{x['med']:+.0f}</td><td>{x['p10']:+.0f}</td><td>{x['worst']:+.0f}</td><td>{x['loss']}</td>"
                         f"<td>{x['mdd']:.0f}</td><td>{x['mdd_worst']:.0f}</td><td>{x['beat_hold']}</td><td>{x['tax']:.0f}</td><td>{x['full']:+.0f}</td>"
                         f"<td>{(x['full_cagr'] or 0):.1f}</td><td>{x['full_mdd']:.0f}</td></tr>")
            h.append("</table></div>")
        h.append("<div><table><tr><th class='l'>투입 기록(전체 기간)</th><th>되사기</th><th>이득</th><th>평균</th><th>최악</th><th>연속 손해</th><th>추가 매수</th><th>실패</th><th>평균</th><th>실패 평균</th><th>최악</th><th>연속 실패</th><th>기간</th><th>이자 비용 포함 평균</th></tr>")
        for r, e in d["ev"].items():
            c, j, jc = e["cut"], e["inj"], e["inj_cost"]
            f = lambda v: "-" if v is None else f"{v:+.1f}"
            h.append(f"<tr><td class='l'>{r}</td><td>{c.get('n', 0)}</td><td>{c.get('win', '-')}</td><td>{f(c.get('mean'))}</td><td>{f(c.get('worst'))}</td><td>{c.get('streak', '-')}</td>"
                     f"<td>{j.get('n', 0)}</td><td>{j.get('fail', '-')}</td><td>{f(j.get('mean'))}</td><td>{f(j.get('fail_mean'))}</td><td>{f(j.get('worst'))}</td><td>{j.get('streak', '-')}</td>"
                     f"<td>{j.get('streak_from') or ''}~{j.get('streak_to') or ''}</td><td>{f(jc.get('mean'))}</td></tr>")
        h.append("</table></div>")
    open("results/index_cost.html", "w", encoding="utf-8").write("\n".join(h))


if __name__ == "__main__":
    import builtins
    import traceback
    _p = builtins.print
    builtins.print = lambda *a, **k: _p("::notice::" + " ".join(str(x) for x in a))
    try:
        main()
    except Exception:
        for ln in traceback.format_exc().splitlines()[-8:]:
            _p("::error::" + ln)
        raise
