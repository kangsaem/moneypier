"""역·역·역 RSI 매수 기준 시험(2026-11-06, 사용자 질문: 국장 RSI 30 기준이 빡빡한가)

V1은 일·주·월 모두 역배열(역·역·역)이면 매수 '싼 조건' RSI 기준을 35 → 30으로 낮춤(market_report.RSI_LO_WEAK).
같은 종목·같은 기간에서 이 값만 30(현행) · 33 · 35(조이지 않음)로 바꿔 신호를 다시 계산하고 비교한다. 다른 규칙은 그대로.
표본·계좌 = variant_cmp.py와 같음(VC_SRC rand20 | fixed, VC_MARKET, VC_SEED, RC_START). 미래 정보 없음(그날까지 데이터만).

비교 1) 계좌: 실전 규칙(variant_cmp.sim 기본 = portfolio.simulate_alloc held=(150,10,2))의 수익·MDD·매수 횟수
비교 2) 신호: 'v1·매수검토'(매수·바닥+매수·눌림) 새로 켜진 날 5·20·60거래일 뒤 수익 — 현행 신호 vs 완화로 '추가된' 신호
결과: results/rsiw_<원천>_<시장>_<기간 또는 번호>.json
"""
import json
import os
import time

import numpy as np
import pandas as pd

import variant_cmp as vc       # VC_* 환경변수로 표본 설정(random_cmp 포함)

m, b, rc = vc.m, vc.b, vc.rc
LEVELS = (30, 33, 35)
BUY = "v1·매수검토"
_LOAD = m.load_prices


def load(cand, start, end, fixed, lo_weak):
    """variant_cmp.load_states와 같음 + RSI_LO_WEAK 바꿔서 + 신호 이벤트도 돌려줌(fork라 자식 프로세스에 그대로 전달)"""
    m.RSI_LO_WEAK = lo_weak
    old = rc.marcap_prices([t["code"] for t in cand], start.year - 2, end.year) if (vc.MKT == "kospi" and fixed) else {}

    def _ld(code):
        try:
            df = _LOAD(code)
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
    states, names, events = {}, [], []
    for i, t in enumerate(cand):
        r = res.get(i)
        if r and r[0] == "ok" and len(states) < 20:
            ev, _, stt = r[1]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
            names.append(t["name"])
            events += [e for e in ev if e["cat"] == BUY and start <= e["date"] <= end]
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    dates = dates[(dates >= start) & (dates <= end)]
    return states, names, events, dates


def fstats(evs):
    out = {"n": len(evs)}
    for h in (5, 20, 60):
        v = [e[f"r{h}"] for e in evs if e.get(f"r{h}") is not None]
        out[f"r{h}"] = float(np.mean(v)) if v else None
        out[f"med{h}"] = float(np.median(v)) if v else None
        out[f"win{h}"] = float(np.mean([x > 0 for x in v]) * 100) if v else None
    return out


def main():
    t0 = time.time()
    fixed = vc.SRC == "fixed"
    cand, start, end, note = vc.from_fixed() if fixed else vc.from_rand20()
    if fixed:
        cand = cand[:45]
    same = vc.MKT == "kospi"
    runs, evk = {}, {}
    names0 = None
    for lv in LEVELS:
        states, names, events, dates = load(cand, start, end, fixed, lv)
        names0 = names0 or names
        if names != names0:                                    # 표본이 달라지면 비교 무의미 → 기록만
            print("표본 다름", lv)
        V = vc.sim(states, dates, same, diag=True)
        V.pop("curve", None)
        keyed = {(e["code"], e["date"]): e for e in events}
        evk[lv] = keyed
        runs[str(lv)] = {"acct": V, "sig": fstats(list(keyed.values()))}
    base = evk[LEVELS[0]]
    for lv in LEVELS[1:]:
        extra = [e for k, e in evk[lv].items() if k not in base]
        gone = [e for k, e in base.items() if k not in evk[lv]]   # 완화하면 앞당겨져 현행 날짜에서 빠지는 신호(쿨다운)
        runs[str(lv)]["extra"] = fstats(extra)
        runs[str(lv)]["gone"] = fstats(gone)
        runs[str(lv)]["extra_list"] = [{"name": e["name"], "date": f"{e['date']:%Y-%m-%d}", "rsi": round(e["rsi"], 1),
                                        **{f"r{h}": (None if e.get(f"r{h}") is None else round(e[f"r{h}"], 1)) for h in (5, 20, 60)}}
                                       for e in sorted(extra, key=lambda e: e["date"])][:200]
    out = {"src": vc.SRC, "market": vc.MKT, "seed": vc.SEED, "note": note, "window": f"{dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}",
           "n": len(names0 or []), "names": names0, "levels": list(LEVELS), "runs": runs, "secs": round(time.time() - t0)}
    tag = f"{dates[0]:%Y%m}_s{vc.SEED}" if fixed else f"s{vc.SEED}"
    os.makedirs("results", exist_ok=True)
    with open(f"results/rsiw_{vc.SRC}_{vc.MKT}_{tag}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False)
    print(out["window"], {lv: round(runs[str(lv)]["acct"]["roc"], 1) for lv in LEVELS},
          {lv: runs[str(lv)].get("extra", {}).get("n") for lv in LEVELS[1:]}, "secs", out["secs"])


if __name__ == "__main__":
    main()
