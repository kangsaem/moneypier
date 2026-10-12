"""V1 규칙 변형 시험(2026-10-13, 리포트·기록과 무관한 별도 시험)

질문 1) V1이 같은 종목 '그냥 보유'에 지는 원인: 손절·매도 신호·잦은 매매 중 무엇이 수익을 깎나
질문 2) 시장이 나쁠 때 현금을 늘리면(지수 200일선·지수 상태·계좌 하락) 덜 잃고 얼마나 덜 버나

같은 종목·같은 기간·같은 신호를 한 번 계산하고, 계좌 규칙만 바꿔 여러 번 돌린다(차이 = 규칙 차이).
표본: VC_SRC = rand20(어젯밤 rand20 결과의 기간·종목 그대로, VC_SEED 번호) | fixed(RC_START부터 5년, random_cmp와 같은 방식으로 뽑음)
시장: VC_MARKET = kospi | nasdaq. 지수 = 코스피(KS11) / 나스닥100(^NDX), 그날까지의 지수만 씀(미래 정보 없음).

계좌 공통 = 실전 규칙(portfolio.simulate_alloc held=(150,10,2)와 같음): 종목당 상한 min(10%, 150 ÷ 보유 종목 수), 신호당 그 절반,
상한 여유 < 1/4이면 건너뜀, 현금 < 살 금액의 절반이면 건너뜀, 매수 0.05%·매도 0.25%, 국내 당일 종가·해외 다음 날 종가.
변형(사전에 정한 값, 결과 보고 바꾸지 않음):
  base 기본 V1 · nostop 손절 없음 · stop15 손절 −15% · nosell 매도 신호 무시(손절만) · buyonly 신호에 사고 팔지 않음
  fA 지수 200일선 아래면 새로 사지 않음
  fB50·fB40·fB30 지수 200일선 아래로 가면 현금을 그 비율까지 늘림(보유 종목을 같은 비율로 팔아서), 그동안 그 이상 사지 않음. 위로 오면 제한 해제
  dd50·dd40·dd30 계좌가 고점 대비 −15%면 현금을 그 비율까지(팔아서), 지수가 50일선 위로 오면 해제하고 고점을 다시 셈
  s1_50·40·30 현금 하한(수동형): 지수 200일선 아래면 살 때 현금이 그 비율 아래로 내려가지 않게만(팔지 않음)
  s2_50·40·30 현금 하한(수동형): 지수 상태별 상승추세 0 · 쉬어감 10 · 없음 20 · 바닥 다짐 30 · 하락 중 50%(최고값을 50·40·30으로 비례 축소)
  비교: hold 그냥 보유(첫날 같은 금액) · holdB50·40·30 보유 + 200일선 아래면 그 비율까지 팔고 위로 오면 다시 사서 되돌림 · dca 적립(여섯 경우 평균)
진단(base): 매매 횟수·비용, 판 뒤 20·60거래일 그 종목 수익(신호·손절별), 손절 뒤 20거래일 안에 더 비싸게 다시 산 횟수
결과: results/var_<원천>_<시장>_<기간 또는 번호>.json
"""
import json
import os
import time

SRC = os.environ.get("VC_SRC", "rand20")
MKT = os.environ.get("VC_MARKET", "kospi")
SEED = int(os.environ.get("VC_SEED", "1"))
os.environ["RC_MARKET"] = MKT
os.environ["RC_SEED"] = str(SEED)
os.environ["RC_N"] = "20"
os.environ["RC_WINDOW"] = "fixed" if SRC == "fixed" else "random"

import numpy as np             # noqa: E402
import pandas as pd            # noqa: E402

import random_cmp as rc        # noqa: E402  (RC_* 환경변수로 설정됨)
b, m, pf = rc.b, rc.m, rc.pf

BUY_C, SELL_C = pf.BUY_COST, pf.SELL_COST
AB, AS = set(pf.ALLOC_BUY), set(pf.ALLOC_SELL)
S2_MAP = {"up": 0.0, "rest": 0.1, "none": 0.2, "base": 0.3, "down": 0.5}


# ---------------------------------------------------------------- 표본
def from_rand20():
    """어젯밤 rand20 결과의 기간·종목을 그대로(국내는 FDR 시세 — rand20과 같은 원천)"""
    d = json.load(open(f"results/rand20_{MKT}_s{SEED}.json", encoding="utf-8"))
    t0, t1 = pd.Timestamp(d["window"][:10]), pd.Timestamp(d["window"][-10:])
    if MKT == "nasdaq":
        cand = [{"code": b.N100_RENAME.get(n, n), "name": n} for n in d["names"]]
    else:
        _, lst = b.kospi_top_at(t0, 300)
        by = {x["name"]: x["code"] for x in lst}
        cand = [{"code": by[n], "name": n} for n in d["names"] if n in by]
    return cand, t0, t1, f"rand20 번호 {SEED}"


def from_fixed():
    cand, note, start = rc.pick()
    return cand, start, rc.END, note


def load_states(cand, start, end, fixed):
    _load = m.load_prices
    old = rc.marcap_prices([t["code"] for t in cand], start.year - 2, end.year) if (MKT == "kospi" and fixed) else {}

    def _ld(code):
        try:
            df = _load(code)
        except Exception:
            df = pd.DataFrame()
        df = df[df.index <= end] if len(df) else df
        if code in old and len(old[code]) > 300:
            df = old[code][old[code].index <= end]
        return df
    m.load_prices = _ld
    b.BT_DAYS = 1450
    for t in cand:
        b.bench_for(t["code"])
    res = dict(b.run_all(cand))
    states, names, failed = {}, [], []
    for i, t in enumerate(cand):
        r = res.get(i)
        if r and r[0] == "ok" and len(states) < 20:
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
            names.append(t["name"])
        elif len(states) < 20:
            failed.append(t["name"])
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    dates = dates[(dates >= start) & (dates <= end)]
    return states, names, failed, dates


# ---------------------------------------------------------------- 지수
def index_info(dates, start):
    """지수 종가 → 200일선·50일선 위/아래, 지수 상태(그날까지 종가로) — 시뮬레이션 날짜에 맞춤(쉬는 날은 직전 값)"""
    if MKT == "kospi":
        ix = m._index_close("KS11", "^KS11").dropna()
    else:
        import yfinance as yf
        ix = yf.Ticker("^NDX").history(start=m.START)["Close"].dropna()
        ix.index = ix.index.tz_localize(None)
    ix = ix[ix.index <= dates[-1]]
    ma200, ma50 = ix.rolling(200).mean(), ix.rolling(50).mean()
    st = {}
    df = pd.DataFrame({"Close": ix})
    lo = max(300, ix.index.searchsorted(start) - 5)
    for t in range(lo, len(ix)):
        try:
            r = m.analyze_df(df.iloc[: t + 1], "IDX", "IDX", light=True)
            s = m.index_state(r)
            st[ix.index[t]] = s["key"] if s else "none"
        except Exception:
            st[ix.index[t]] = "none"
    al = lambda s: s.reindex(dates, method="ffill")
    below200 = al((ix < ma200) & ma200.notna()).fillna(False).to_numpy(dtype=bool)
    above50 = al((ix > ma50) & ma50.notna()).fillna(True).to_numpy(dtype=bool)
    state = al(pd.Series(st)).fillna("none").to_numpy()
    ixr = float(ix.reindex(dates, method="ffill").iloc[-1] / ix.reindex(dates, method="ffill").iloc[0] - 1) * 100
    return below200, above50, state, ixr


# ---------------------------------------------------------------- 계좌(실전 규칙 + 변형)
def sim(states, dates, same_day, stop=0.92, use_sell=True, buy_only=False, floor=None, force=None, dd=None, diag=False):
    """floor(i) = 살 때 지킬 현금 하한(0~1, 수동) / force(i) = 그날 맞출 현금 비중(팔아서), None이면 없음 /
    dd = (현금 비율, above50 배열): 계좌 고점 대비 −15%면 그 비율까지 팔고 지수 50일선 위로 오면 해제"""
    codes = list(states)
    px = {c: states[c]["close"].reindex(dates).ffill().to_numpy(dtype=float) for c in codes}
    cats = {c: list(states[c]["cats"].reindex(dates)) for c in codes}
    n = len(dates)
    cash, pos, orders, sells_log, buys_log = 1.0, {}, [], [], []
    prev = {c: set() for c in codes}
    eqs, cr = [], []
    ntr = 0
    peak, dd_on = 1.0, False

    def val(i):
        return sum(P["sh"] * px[c][i] for c, P in pos.items() if not np.isnan(px[c][i]))

    def sell_all(c, i, why):
        nonlocal cash, ntr
        P = pos.pop(c)
        cash += P["sh"] * px[c][i] * (1 - SELL_C)
        ntr += 1
        sells_log.append((c, i, px[c][i], why))

    def trim_to(i, f):
        """현금이 평가액의 f가 되도록 보유 종목을 같은 비율로 판다"""
        nonlocal cash
        E = cash + val(i)
        need = f * E - cash
        hv = val(i)
        if need <= 1e-9 or hv <= 0:
            return
        k = min(1.0, need / hv / (1 - SELL_C))
        for c in list(pos):
            p = px[c][i]
            if np.isnan(p):
                continue
            q = pos[c]["sh"] * k
            cash += q * p * (1 - SELL_C)
            pos[c]["sh"] -= q
            if pos[c]["sh"] <= 1e-12:
                pos.pop(c)

    def fl(i):
        f = floor(i) if floor else 0.0
        if force:
            g = force(i)
            if g is not None:
                f = max(f, g)
        if dd_on:
            f = max(f, dd[0])
        return f

    def execute(i):
        nonlocal cash
        for kind, c in sorted(orders, key=lambda o: o[0] != "sell"):
            p = px[c][i]
            if np.isnan(p) or p <= 0:
                continue
            if kind == "sell":
                if c in pos:
                    sell_all(c, i, "신호")
                continue
            E = cash + val(i)
            nh = len(pos) + (0 if c in pos else 1)
            cap = min(10.0, 150.0 / nh)
            want = E * cap / 2 / 100
            have = pos[c]["sh"] * p if c in pos else 0.0
            amt = min(want, E * cap / 100 - have)
            if amt < want * 0.25:
                continue
            room = cash - fl(i) * E                # 현금 하한을 지키고 쓸 수 있는 돈
            if room < amt * 0.5 or cash < amt * 0.5:
                continue
            amt = min(amt, cash, room)
            cash -= amt
            sh = amt * (1 - BUY_C) / p
            if c in pos:
                P = pos[c]
                P["entry"] = (P["entry"] * P["sh"] + p * sh) / (P["sh"] + sh)
                P["sh"] += sh
            else:
                pos[c] = {"sh": sh, "entry": p, "warn": False}
            pos[c]["last"] = i
            buys_log.append((c, i, p))

    for i in range(n):
        if not same_day:
            execute(i)
            orders = []
        if stop is not None and not buy_only:
            for c in list(pos):
                P, x = pos[c], px[c][i]
                if np.isnan(x) or P.get("last") == i:
                    continue
                if x <= P["entry"] * stop:
                    if P["warn"]:
                        sell_all(c, i, "손절")
                        continue
                    P["warn"] = True
                else:
                    P["warn"] = False
        for c in codes:
            on = cats[c][i]
            on = on if isinstance(on, (set, frozenset)) else set()
            new = on - prev[c]
            prev[c] = on
            if i == 0:
                continue
            if c in pos and new & AS:
                if use_sell and not buy_only:
                    orders.append(("sell", c))
            elif new & AB and not (on & AS):
                orders.append(("buy", c))
        if same_day:
            execute(i)
            orders = []
        # 현금 맞추기(팔아서): 지수 필터 B · 계좌 하락 제한
        if force:
            g = force(i)
            if g is not None:
                trim_to(i, g)
        if dd:
            E = cash + val(i)
            if dd_on and dd[1][i]:
                dd_on, peak = False, E
            peak = max(peak, E)
            if not dd_on and E < peak * 0.85:
                dd_on = True
            if dd_on:
                trim_to(i, dd[0])
        E = cash + val(i)
        eqs.append(E)
        cr.append(cash / E if E > 0 else 1.0)
    v = pd.Series(eqs, index=dates)
    out = {"roc": (v.iloc[-1] - 1) * 100, "mdd": float(((v / v.cummax()) - 1).min() * 100), "cash": float(np.mean(cr) * 100),
           "trades": ntr, "buys": len(buys_log)}
    if diag:
        fw = lambda c, i, h: (px[c][min(i + h, n - 1)] / px[c][i] - 1) * 100 if i + h < n else None
        dg = {}
        for why in ("신호", "손절"):
            xs = [(fw(c, i, 20), fw(c, i, 60)) for c, i, p, w in sells_log if w == why]
            a20 = [x[0] for x in xs if x[0] is not None]
            a60 = [x[1] for x in xs if x[1] is not None]
            dg[why] = {"n": len(xs), "after20": float(np.mean(a20)) if a20 else None, "after60": float(np.mean(a60)) if a60 else None}
        rebuy = 0
        for c, i, p, w in sells_log:
            if w == "손절" and any(c2 == c and i < j <= i + 20 and p2 > p for c2, j, p2 in buys_log):
                rebuy += 1
        dg["stop_rebuy_higher"] = rebuy
        dg["n_buys"], dg["n_sells"] = len(buys_log), ntr
        out["diag"] = dg
    out["curve"] = [round(float(x), 4) for x in v.iloc[::5]]
    return out


def hold_sim(states, dates, target=None, below=None):
    """그냥 보유(첫날 같은 금액). target·below가 있으면 지수 200일선 아래로 가면 현금 target까지 팔고, 위로 오면 다시 사서 되돌림"""
    codes = list(states)
    px = pd.DataFrame({c: states[c]["close"] for c in codes}).reindex(dates).ffill()
    px = px.bfill().to_numpy(dtype=float)
    n, k = len(dates), len(codes)
    sh = np.full(k, 1.0 / k * (1 - BUY_C)) / px[0]
    cash, reduced, flips, eqs = 0.0, False, 0, []
    for i in range(n):
        p = px[i]
        hv = float((sh * p).sum())
        if target is not None:
            if below[i] and not reduced:
                E = cash + hv
                need = target * E - cash
                if need > 0 and hv > 0:
                    f = min(1.0, need / hv / (1 - SELL_C))
                    cash += float((sh * f * p).sum()) * (1 - SELL_C)
                    sh = sh * (1 - f)
                reduced, flips = True, flips + 1
            elif not below[i] and reduced:
                hv = float((sh * p).sum())
                w = (sh * p) / hv if hv > 0 else np.full(k, 1.0 / k)
                sh = sh + w * cash * (1 - BUY_C) / p
                cash, reduced = 0.0, False
        eqs.append(cash + float((sh * p).sum()))
    v = pd.Series(eqs, index=dates)
    return {"roc": (v.iloc[-1] - 1) * 100, "mdd": float(((v / v.cummax()) - 1).min() * 100), "flips": flips,
            "curve": [round(float(x), 4) for x in v.iloc[::5]]}


def main():
    t0 = time.time()
    fixed = SRC == "fixed"
    cand, start, end, note = from_fixed() if fixed else from_rand20()
    if fixed:
        cand = cand[:45]
    states, names, failed, dates = load_states(cand, start, end, fixed)
    below, above50, state, ixr = index_info(dates, start)
    same = MKT == "kospi"
    V = {}
    V["base"] = sim(states, dates, same, diag=True)
    V["nostop"] = sim(states, dates, same, stop=None)
    V["stop15"] = sim(states, dates, same, stop=0.85)
    V["nosell"] = sim(states, dates, same, use_sell=False)
    V["buyonly"] = sim(states, dates, same, buy_only=True)
    V["fA"] = sim(states, dates, same, floor=lambda i: 1.0 if below[i] else 0.0)
    for f in (50, 40, 30):
        V[f"fB{f}"] = sim(states, dates, same, force=lambda i, f=f: f / 100 if below[i] else None)
        V[f"dd{f}"] = sim(states, dates, same, dd=(f / 100, above50))
        V[f"s1_{f}"] = sim(states, dates, same, floor=lambda i, f=f: f / 100 if below[i] else 0.0)
        V[f"s2_{f}"] = sim(states, dates, same, floor=lambda i, f=f: S2_MAP.get(state[i], 0.2) * f / 50)
    H = {"hold": hold_sim(states, dates)}
    for f in (50, 40, 30):
        H[f"holdB{f}"] = hold_sim(states, dates, f / 100, below)
    pxd = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
    runs = [rc.dca(pxd, k) for k in rc.NTH]
    dca = {"roc": float(np.mean([r_["roc"] for r_ in runs])), "mdd": float(np.mean([r_["mdd"] for r_ in runs]))}
    # 기본 V1이 portfolio.simulate_alloc 과 같은지(검산)
    eq, *_ = pf.simulate_alloc(states, dates, 0, 0, same_day=same, held=(150, 10, 2))
    chk = float((eq.iloc[-1] / eq.iloc[0] - 1) * 100)
    out = {"src": SRC, "market": MKT, "seed": SEED, "note": note, "window": f"{dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}",
           "n": len(states), "names": names, "failed": failed, "index_ret": ixr,
           "below200_pct": float(np.mean(below) * 100), "state_days": {k: int((state == k).sum()) for k in S2_MAP},
           "v": V, "h": H, "dca": dca, "check_base_vs_pf": [V["base"]["roc"], chk], "secs": round(time.time() - t0)}
    tag = f"{dates[0]:%Y%m}_s{SEED}" if fixed else f"s{SEED}"
    os.makedirs("results", exist_ok=True)
    with open(f"results/var_{SRC}_{MKT}_{tag}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False)
    print(out["window"], "base", round(V["base"]["roc"], 1), "pf", round(chk, 1), "hold", round(H["hold"]["roc"], 1), "secs", out["secs"])


if __name__ == "__main__":
    main()
