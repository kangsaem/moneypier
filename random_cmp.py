"""무작위 30종목: V1 vs 적립식 (2026-10-12, 리포트·기록과 무관한 별도 시험)

RC_MARKET = kospi | nasdaq, RC_SEED = 무작위 번호, 기간 5년(1260거래일).
종목 고르기(그 시작일 당시 목록에서 무작위 — 생존 편향 없음):
  코스피 = 시작일 시가총액 상위 300 중 무작위, 나스닥 = 시작일 나스닥100 구성 중 무작위. 시세 실패(상장폐지 등)는 빼고 30개를 채움.
비교(원금 100, 같은 종목 30개):
  V1 = 최종 규칙(portfolio.simulate_alloc held=(150,10,2), 손절 8%), 현금 100에서 시작
  적립 = 100을 개월 수로 나눠 매달 n번째 거래일(1·5·10·15·20·25, 그달 거래일이 모자라면 마지막 날) 종가에 시세 있는 종목 전부 균등. 팔지 않음
지표: 넣은 돈 대비 수익, 연 환산(IRR), 최대 하락(평가액 기준)
결과: results/rand_<시장>_s<번호>.html·json
"""
import json
import os
import random
import time
from datetime import datetime, timedelta, timezone

MK = os.environ.get("RC_MARKET", "kospi")
SEED = int(os.environ.get("RC_SEED", "1"))
N = int(os.environ.get("RC_N", "30"))
os.environ["BT_UNIVERSE"] = "tickers"
os.environ["BT_DAYS"] = "1260"
os.environ["BT_OUT"] = "results/_tmp_rand"

import pandas as pd            # noqa: E402

import backtest as b           # noqa: E402
import market_report as m      # noqa: E402
import portfolio as pf         # noqa: E402

NTH = (1, 5, 10, 15, 20, 25)


def pick():
    rnd = random.Random(f"{MK}-{SEED}")
    if MK == "kospi":
        ks = m._index_close("KS11", "^KS11")
        day = ks.index[-1260]
        d0, lst = b.kospi_top_at(day, 300)
        note = f"{d0:%Y-%m-%d} 코스피 시가총액 상위 300 중 무작위"
    else:
        from nasdaq_100_ticker_history import tickers_as_of
        ix = m._index_close("IXIC", "^IXIC")
        day = ix.index[-1260]
        tk = sorted(t for t in tickers_as_of(day.year, day.month, day.day) if t not in b.N100_DUP)
        lst = [{"code": b.N100_RENAME.get(t, t), "name": t} for t in tk]
        note = f"{day:%Y-%m-%d} 나스닥100 구성 중 무작위"
    rnd.shuffle(lst)
    return lst[: N + 15], note          # 시세 실패에 대비해 여유분


def dca(px, nth):
    dates = px.index
    months = pd.Series(range(len(dates)), index=dates).groupby([dates.year, dates.month]).apply(list)
    buy_i = set(lst[min(nth, len(lst)) - 1] for lst in months)
    amt = 1.0 / len(buy_i)
    sh = pd.Series(0.0, index=px.columns)
    vals, flows = [], []
    for i, d in enumerate(dates):
        if i in buy_i:
            row = px.iloc[i].dropna()
            row = row[row > 0]
            if len(row):
                sh[row.index] += amt / len(row) * (1 - pf.BUY_COST) / row
                flows.append((d, amt))
        vals.append(float((sh * px.iloc[i].fillna(0)).sum()) + 1.0 - sum(a for _, a in flows))
    v = pd.Series(vals, index=dates)
    endv = float((sh * px.iloc[-1].fillna(0)).sum())
    t0 = flows[0][0]
    yrs = lambda d: (d - t0).days / 365.25
    irr = pf._irr([(yrs(d), -a) for d, a in flows] + [(yrs(dates[-1]), endv)])
    return {"nth": nth, "roc": (v.iloc[-1] - 1) * 100, "irr": irr, "mdd": float(((v / v.cummax()) - 1).min() * 100)}


def main():
    t0 = time.time()
    cand, note = pick()
    for t in cand:
        b.bench_for(t["code"])
    res = dict(b.run_all(cand))
    states, names = {}, []
    for i, t in enumerate(cand):
        r = res.get(i)
        if r and r[0] == "ok" and len(states) < N:
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
            names.append(t["name"])
    dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    eq, trades, cash, open_n, _ = pf.simulate_alloc(states, dates, 0, 0, same_day=(MK == "kospi"), held=(150, 10, 2))
    mt = pf.metrics(eq, trades, cash, open_n)
    yrs = (dates[-1] - dates[0]).days / 365.25
    v1 = {"roc": mt["tot"], "irr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100, "mdd": mt["mdd"], "trades": mt["n"], "cash": mt["cash"]}
    px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates).ffill()
    runs = [dca(px, n) for n in NTH]
    out = {"market": MK, "seed": SEED, "n": len(states), "names": names, "note": note, "window": f"{dates[0]:%Y-%m-%d}~{dates[-1]:%Y-%m-%d}",
           "v1": v1, "dca": runs, "secs": round(time.time() - t0),
           "run": (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")}
    lo = lambda k: min(r[k] for r in runs if r[k] is not None)
    hi = lambda k: max(r[k] for r in runs if r[k] is not None)
    out["winner"] = "V1" if v1["irr"] > hi("irr") else "적립" if v1["irr"] < lo("irr") else "비슷(적립 범위 안)"
    os.makedirs("results", exist_ok=True)
    name = f"rand_{MK}_s{SEED}"
    with open(f"results/{name}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0)
    f = lambda v: "-" if v is None else f"{v:+.1f}%"
    h = [f"<!doctype html><meta charset='utf-8'><title>{name}</title><style>body{{font:13px sans-serif;margin:16px}}td,th{{border:1px solid #ccc;padding:3px 7px;text-align:right}}td.l{{text-align:left}}table{{border-collapse:collapse}}</style>",
         f"<h1>무작위 {out['n']}종목 · {MK} · 번호 {SEED} · {out['window']}</h1><p>{note} · 더 나은 쪽: <b>{out['winner']}</b></p>",
         "<table><tr><th class='l'>방식</th><th>넣은 돈 대비</th><th>연 환산</th><th>최대 하락</th></tr>"
         f"<tr><td class='l'>V1 (현금 100에서 시작)</td><td>{f(v1['roc'])}</td><td>{f(v1['irr'])}</td><td>{v1['mdd']:.1f}%</td></tr>"
         + "".join(f"<tr><td class='l'>적립 매달 {r['nth']}번째 거래일</td><td>{f(r['roc'])}</td><td>{f(r['irr'])}</td><td>{r['mdd']:.1f}%</td></tr>" for r in runs)
         + "</table><p>종목: " + ", ".join(names) + "</p>"]
    with open(f"results/{name}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))
    print(name, out["winner"], json.dumps(v1, ensure_ascii=False), f"적립 연 {lo('irr'):+.1f}~{hi('irr'):+.1f}%")


if __name__ == "__main__":
    main()
