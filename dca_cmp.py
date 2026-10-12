"""V1 계좌 vs 무지성 적립 (2026-10-12, 리포트·기록과 무관한 별도 시험)

같은 종목 묶음(DC_UNIVERSE: tickers | kospi | nasdaq), 같은 원금 100, 1·3·5년.
- V1: 최종 규칙(portfolio.simulate_alloc held=(150,10,2), 손절 8%), 시작일 현금 100%
- 무지성 적립: 원금 100을 기간 개월 수로 나눠 매달 같은 금액을, 매달 n번째 거래일(1·5·10·15·20) 종가에
  그날 시세가 있는 종목 전부에 똑같이 나눠 산다. 팔지 않음. 아직 안 넣은 돈은 현금(이자 없음)
- 참고: 균등 일시 보유(시작일에 100 전부), 지수 적립(같은 방식으로 지수 하나)
지표: 최종 평가액(원금 100 기준), 넣은 돈 대비(= 최종 − 100), 연 환산(IRR, 실제 넣은 시점 기준), 최대 하락(평가액 기준, 적립은 넣은 돈 효과 포함)
결과: results/dca_cmp_<대상>.html·json
"""
import json
import os
import time
from datetime import datetime, timedelta, timezone

UNI = os.environ.get("DC_UNIVERSE", "tickers")
os.environ["BT_UNIVERSE"] = UNI
os.environ["BT_DAYS"] = "1260"
os.environ.setdefault("BT_TOP", "50")
os.environ["BT_OUT"] = "results/_tmp_dca"

import pandas as pd            # noqa: E402

import backtest as b           # noqa: E402
import market_report as m      # noqa: E402
import portfolio as pf         # noqa: E402

PERIODS = ((252, "1년"), (756, "3년"), (1260, "5년"))
NTH = (1, 5, 10, 15, 20)
BUY_COST = pf.BUY_COST


def irr(flows, end_date, end_val):
    t0 = flows[0][0]
    yrs = lambda d: (d - t0).days / 365.25
    return pf._irr([(yrs(d), -a) for d, a in flows] + [(yrs(end_date), end_val)])


def dca(px, nth):
    """px: 날짜 × 종목 종가(없으면 NaN). 매달 n번째 거래일에 (100 ÷ 개월 수)를 그날 시세 있는 종목에 균등"""
    dates = px.index
    months = pd.Series(range(len(dates)), index=dates).groupby([dates.year, dates.month]).apply(list)
    buy_i = [lst[min(nth, len(lst)) - 1] for lst in months]
    amt = 1.0 / len(buy_i)
    sh = pd.Series(0.0, index=px.columns)
    vals, flows, bi = [], [], set(buy_i)
    for i, d in enumerate(dates):
        if i in bi:
            row = px.iloc[i].dropna()
            row = row[row > 0]
            if len(row):
                sh[row.index] += amt / len(row) * (1 - BUY_COST) / row
                flows.append((d, amt))
        val = float((sh * px.iloc[i].fillna(0)).sum())
        cash_left = 1.0 - sum(a for _, a in flows)
        vals.append(val + cash_left)                   # 아직 안 넣은 돈은 현금으로 들고 있다고 봄(평가액 = 주식 + 남은 현금)
    v = pd.Series(vals, index=dates)
    end = v.iloc[-1]
    return {"end": end * 100, "roc": (end - 1) * 100, "irr": irr(flows, dates[-1], float((sh * px.iloc[-1].fillna(0)).sum())),
            "mdd": float(((v / v.cummax()) - 1).min() * 100)}


def main():
    t0 = time.time()
    tickers = b.load_universe()
    for t in tickers:
        b.bench_for(t["code"])
    res = dict(b.run_all(tickers))
    states = {}
    for i, t in enumerate(tickers):
        r = res.get(i)
        if r and r[0] == "ok":
            stt = r[1][2]
            states[t["code"]] = {"close": stt["close"], "cats": stt["cats"]}
    idx = m._index_close("KS11", "^KS11") if UNI != "nasdaq" else m._index_close("IXIC", "^IXIC")
    all_dates = pd.DatetimeIndex(sorted({d for s in states.values() for d in s["close"].index}))
    out = {"universe": UNI, "n": len(states), "periods": {}}
    for P, pname in PERIODS:
        cut = all_dates[-min(P, len(all_dates))]
        dates = all_dates[all_dates >= cut]
        sts = {}
        for c, s in states.items():
            cats = s["cats"]
            if UNI == "tickers" and not m.is_kr(c):
                cats = cats.shift(1).apply(lambda x: x if isinstance(x, (set, frozenset)) else set())
            sts[c] = {"close": s["close"], "cats": cats}
        eq, trades, cash, open_n, _ = pf.simulate_alloc(sts, dates, 0, 0, same_day=UNI in ("kospi", "tickers"), held=(150, 10, 2))
        v1 = pf.metrics(eq, trades, cash, open_n)
        yrs = (dates[-1] - dates[0]).days / 365.25
        v1_row = {"end": float(eq.iloc[-1] / eq.iloc[0] * 100), "roc": v1["tot"], "irr": ((eq.iloc[-1] / eq.iloc[0]) ** (1 / yrs) - 1) * 100 if yrs > 0 else None,
                  "mdd": v1["mdd"], "cash": v1["cash"]}
        px = pd.DataFrame({c: s["close"] for c, s in states.items()}).reindex(dates)
        pxf = px.ffill()
        runs = [dca(pxf.where(px.notna() | pxf.notna()), n) for n in NTH]
        eqw = (pxf / pxf.bfill().iloc[0]).mean(axis=1)
        lump = {"end": float(eqw.iloc[-1] * 100), "roc": float((eqw.iloc[-1] - 1) * 100),
                "irr": ((eqw.iloc[-1]) ** (1 / yrs) - 1) * 100 if yrs > 0 else None, "mdd": float(((eqw / eqw.cummax()) - 1).min() * 100)}
        ix = idx.reindex(dates, method="ffill").to_frame("idx")
        iruns = [dca(ix, n) for n in NTH]
        out["periods"][pname] = {"cut": f"{cut:%Y-%m-%d}", "v1": v1_row, "dca": runs, "lump": lump, "idx_dca": iruns,
                                 "idx_lump": float((ix["idx"].iloc[-1] / ix["idx"].iloc[0] - 1) * 100)}
        rng = lambda rs, k: f"{min(r[k] for r in rs):+.1f}~{max(r[k] for r in rs):+.1f}"
        print(f"== {pname} {cut:%Y-%m-%d}~ V1 {v1_row['roc']:+.1f}% (연 {v1_row['irr']:+.1f}%, mdd {v1_row['mdd']:.1f}) · 적립 {rng(runs, 'roc')}% (연 {rng(runs, 'irr')}%) · "
              f"일시 {lump['roc']:+.1f}% · 지수 적립 {rng(iruns, 'roc')}%", flush=True)
    out["secs"] = round(time.time() - t0)
    out["run"] = (datetime.now(timezone.utc) + timedelta(hours=9)).strftime("%Y-%m-%d %H:%M")
    os.makedirs("results", exist_ok=True)
    with open(f"results/dca_cmp_{UNI}.json", "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, indent=0, default=str)
    f = lambda v: "-" if v is None else f"{v:+.1f}%"
    h = [f"<!doctype html><meta charset='utf-8'><title>V1 vs 적립</title><style>body{{font:13px sans-serif;margin:16px}}td,th{{border:1px solid #ccc;padding:3px 7px;text-align:right}}td.l{{text-align:left}}table{{border-collapse:collapse;margin:8px 0}}</style>",
         f"<h1>V1 계좌 vs 무지성 적립 · {UNI} · {out['n']}종목 · 원금 100</h1><p>실행 {out['run']} KST · 적립 = 매달 1·5·10·15·20번째 거래일 다섯 경우의 범위</p>"]
    for pname, P in out["periods"].items():
        rr = lambda rs, k: f"{f(min(r[k] for r in rs if r[k] is not None))} ~ {f(max(r[k] for r in rs if r[k] is not None))}"
        h.append(f"<h2>{pname} ({P['cut']}~)</h2><table><tr><th class='l'>방식</th><th>넣은 돈 대비</th><th>연 환산</th><th>최대 하락</th></tr>"
                 f"<tr><td class='l'>V1 (현금에서 시작)</td><td>{f(P['v1']['roc'])}</td><td>{f(P['v1']['irr'])}</td><td>{P['v1']['mdd']:.1f}%</td></tr>"
                 f"<tr><td class='l'>무지성 적립(같은 종목 균등)</td><td>{rr(P['dca'], 'roc')}</td><td>{rr(P['dca'], 'irr')}</td><td>{rr(P['dca'], 'mdd')}</td></tr>"
                 f"<tr><td class='l'>균등 일시 보유</td><td>{f(P['lump']['roc'])}</td><td>{f(P['lump']['irr'])}</td><td>{P['lump']['mdd']:.1f}%</td></tr>"
                 f"<tr><td class='l'>지수 적립</td><td>{rr(P['idx_dca'], 'roc')}</td><td>{rr(P['idx_dca'], 'irr')}</td><td>{rr(P['idx_dca'], 'mdd')}</td></tr>"
                 f"<tr><td class='l'>지수 일시 보유</td><td>{f(P['idx_lump'])}</td><td>-</td><td>-</td></tr></table>")
    with open(f"results/dca_cmp_{UNI}.html", "w", encoding="utf-8") as fp:
        fp.write("\n".join(h))


if __name__ == "__main__":
    main()
